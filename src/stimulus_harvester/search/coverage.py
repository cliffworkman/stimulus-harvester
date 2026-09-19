"""Descriptive source overlap; no eligibility or scientific ranking decisions."""

from __future__ import annotations

from itertools import combinations


def coverage_report(conn, provider_order=None, operation_id=None):
    where = " WHERE d.operation_id=?" if operation_id else ""
    rows = conn.execute(
        """SELECT d.provider, d.image_url, d.candidate_id, c.asset_id
        FROM discoveries d JOIN candidates c USING(candidate_id)"""
        + where,
        (operation_id,) if operation_id else (),
    ).fetchall()
    providers = sorted({r["provider"] for r in rows})
    order = list(provider_order) if provider_order is not None else providers
    if len(order) != len(set(order)) or not set(providers).issubset(order):
        raise ValueError("Provider order must contain every observed provider exactly once")
    groups = {}
    seen_urls, seen_assets = set(), set()
    for provider in order:
        group = [r for r in rows if r["provider"] == provider]
        urls = {r["image_url"] for r in group}
        assets = {r["asset_id"] for r in group if r["asset_id"]}
        others = [r for r in rows if r["provider"] != provider]
        groups[provider] = {
            "source_occurrences": len(group),
            "candidate_count": len({r["candidate_id"] for r in group}),
            "unique_urls": len(urls),
            "acquired_unique_assets": len(assets),
            "candidate_count_without_acquired_asset": len(
                {r["candidate_id"] for r in group if not r["asset_id"]}
            ),
            "incremental_unique_urls": len(urls - seen_urls),
            "incremental_unique_assets": len(assets - seen_assets),
            "exclusive_urls": len(urls - {r["image_url"] for r in others}),
            "exclusive_assets": len(assets - {r["asset_id"] for r in others if r["asset_id"]}),
        }
        seen_urls |= urls
        seen_assets |= assets
    overlap = []
    for a, b in combinations(order, 2):
        aa = [r for r in rows if r["provider"] == a]
        bb = [r for r in rows if r["provider"] == b]
        overlap.append(
            {
                "providers": [a, b],
                "shared_urls": len({r["image_url"] for r in aa} & {r["image_url"] for r in bb}),
                "shared_exact_assets": len(
                    {r["asset_id"] for r in aa if r["asset_id"]}
                    & {r["asset_id"] for r in bb if r["asset_id"]}
                ),
            }
        )
    return {
        "operation_id": operation_id,
        "provider_order": order,
        "providers": groups,
        "pairwise_overlap": overlap,
        "total_unique_urls": len(seen_urls),
        "total_acquired_assets": len(seen_assets),
        "interpretation": "Descriptive only. Incremental counts depend on the displayed provider order; exclusive counts do not. Asset overlap reflects current acquired hashes, not unverified provider hash claims.",
    }
