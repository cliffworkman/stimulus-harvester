"""Wikimedia Commons MediaWiki API; raw extended metadata remains source evidence."""

from __future__ import annotations

from urllib.parse import quote

from stimulus_harvester.models import SearchResult, make_local_id
from stimulus_harvester.search.http import PagedAdapter, SearchPage, http_url, image_url


class CommonsAdapter(PagedAdapter):
    name = "commons"

    def __init__(self, config, client=None):
        if not http_url(config.base_url):
            raise ValueError("Commons requires a valid HTTP(S) base_url")
        contact = config.contact or ""
        if not (http_url(contact) or ("@" in contact and not any(c.isspace() for c in contact))):
            raise ValueError(
                "Commons requires search.commons.contact (operator email or project URL) for its User-Agent"
            )
        config = config.model_copy(update={"user_agent": f"StimulusHarvesterBot/0.1 ({contact})"})
        self._init_http(config, client)

    def iter_pages(self, query, seed_query, *, raw_api_dir):
        continuation = {}
        offset = 0
        for page in range(1, self.config.max_pages_per_query + 1):
            params = {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "gsrlimit": self.config.page_size,
                "prop": "imageinfo",
                "maxlag": self.config.maxlag,
                "iiprop": "url|size|sha1|timestamp|mime|mediatype|extmetadata",
            }
            params.update(continuation)
            fetched = self.request_page(
                self.config.base_url, params, query, seed_query, page, raw_api_dir
            )
            if fetched is None:
                return
            payload, raw_path, evidence = fetched
            if "warnings" in payload:
                self.emit("provider_warning", warnings=payload["warnings"])
            if "query" not in payload and "batchcomplete" in payload:
                items = []
            else:
                items = (
                    payload.get("query", {}).get("pages")
                    if isinstance(payload.get("query"), dict)
                    else None
                )
            if not isinstance(items, list):
                self.emit(
                    "failed",
                    error={"kind": "invalid_shape", "message": "Commons lacks query.pages array"},
                )
                return
            # Generator index is search order, whereas API page ordering can be by page ID.
            indexed = all(
                isinstance(item, dict) and isinstance(item.get("index"), int) for item in items
            )
            if indexed:
                items = sorted(items, key=lambda item: item["index"])
            results = self.normalize_items(items, query, seed_query, raw_path, page, offset)
            yield SearchPage(results, evidence)
            offset += len(items)
            next_token = payload.get("continue")
            if not next_token:
                return
            if not isinstance(next_token, dict) or not all(
                key in {"continue", "gsroffset", "iicontinue"} and isinstance(value, (str, int))
                for key, value in next_token.items()
            ):
                self.emit(
                    "failed",
                    error={
                        "kind": "invalid_continuation",
                        "message": "Unsupported continuation token",
                    },
                )
                return
            if next_token == continuation:
                self.emit(
                    "failed",
                    error={
                        "kind": "repeated_continuation",
                        "message": "Provider repeated the same continuation",
                    },
                )
                return
            continuation = next_token

    def _normalize(self, item, *, query, seed_query, raw_path):
        info_list = item.get("imageinfo")
        if not isinstance(info_list, list) or not info_list or not isinstance(info_list[0], dict):
            raise ValueError("Commons file has no imageinfo; it may be missing or inaccessible")
        info = info_list[0]
        mime = info.get("mime")
        if not isinstance(mime, str) or not mime.startswith("image/"):
            raise ValueError(f"Commons result is not declared image media: {mime!r}")
        direct = image_url(info.get("url"))
        metadata = info.get("extmetadata") or {}

        def claim(key):
            value = metadata.get(key) if isinstance(metadata, dict) else None
            return (
                value.get("value")
                if isinstance(value, dict) and isinstance(value.get("value"), str)
                else None
            )

        short = claim("LicenseShortName")
        license_url = claim("LicenseUrl")
        license_claim = claim("License")
        heterogeneous = (
            any(
                key in metadata
                and (
                    not isinstance(metadata[key], dict)
                    or not isinstance(metadata[key].get("value"), str)
                )
                for key in ("LicenseShortName", "LicenseUrl", "License")
            )
            if isinstance(metadata, dict)
            else True
        )
        if heterogeneous:
            license_state = "ambiguous"
        elif not any((short, license_url, license_claim)):
            license_state = "missing"
        elif (
            short
            and license_url
            and http_url(license_url)
            and "<" not in short
            and not any(marker in short.lower() for marker in (" or ", " and ", "|", ";"))
        ):
            license_state = "reported_unverified"
        else:
            license_state = "ambiguous"
        return SearchResult(
            local_id=make_local_id(self.name, direct),
            source_backend=self.name,
            external_id=str(item["pageid"]) if item.get("pageid") is not None else None,
            search_query=query,
            seed_query=seed_query,
            title=item.get("title"),
            image_url=direct,
            landing_page_url=info.get("descriptionurl")
            or "https://commons.wikimedia.org/wiki/"
            + quote(item.get("title", "").replace(" ", "_")),
            creator=claim("Artist"),
            license=short,
            license_url=license_url,
            width=info.get("width"),
            height=info.get("height"),
            source_domain="commons.wikimedia.org",
            raw_response_path=str(raw_path),
            provider_metadata={
                "record": item,
                "license_metadata_status": license_state,
                "attribution": claim("Attribution"),
                "credit": claim("Credit"),
                "license_claim": license_claim,
                "creator_representation": "provider HTML/free text",
                "acquisition_validation": "provider_image_mime_and_url_unverified",
                "provider_sha1": info.get("sha1"),
                "provider_timestamp": info.get("timestamp"),
            },
        )
