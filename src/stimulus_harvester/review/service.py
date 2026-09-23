"""Shared review transition used by native, browser and programmatic clients."""

from stimulus_harvester.models import ReviewLabel
from stimulus_harvester.review.db import get_image, set_review_label


def record_review(
    conn,
    local_id,
    *,
    label,
    notes=None,
    scene_cluster_id=None,
    reviewer_email=None,
    require_identity=False,
):
    label = ReviewLabel(label).value
    if get_image(conn, local_id) is None:
        raise ValueError("Unknown candidate/local ID")
    if require_identity and (not reviewer_email or not reviewer_email.strip()):
        raise ValueError("Enter the investigator identity before recording a review")
    return dict(
        set_review_label(
            conn,
            local_id,
            label=label,
            notes=notes,
            scene_cluster_id=scene_cluster_id,
            reviewer_email=reviewer_email,
        )
    )


def record_batch(conn, *, selection, label, reviewer_email, notes=None):
    """Explicit candidate scope, one atomic transaction, append-only per-image history.

    Only provisional Maybe and Reject are batchable. No asset-group propagation,
    automated suitability, screening override, or freeze membership change.
    """
    if label not in {"maybe", "rejected"}:
        raise ValueError("Batch review supports only Maybe or Reject")
    if not reviewer_email or not reviewer_email.strip():
        raise ValueError("Enter the investigator identity before recording a review")
    if not selection or len({r["candidate_id"] for r in selection}) != len(selection):
        raise ValueError("Select distinct candidates for batch review")
    with conn:
        # Lock before checking all prior states; no partially applied batch.
        conn.execute("BEGIN IMMEDIATE")
        locals_to_review = []
        for expected in selection:
            current = conn.execute(
                "SELECT c.primary_local_id,c.asset_id,r.label FROM candidates c "
                "LEFT JOIN reviews r ON r.local_id=c.primary_local_id WHERE c.candidate_id=?",
                (expected["candidate_id"],),
            ).fetchone()
            if (
                current is None
                or not current["asset_id"]
                or current["asset_id"] != expected["asset_id"]
                or current["label"] != expected["label"]
            ):
                raise ValueError(
                    "Selected candidate changed or is unavailable; refresh and preview the batch again"
                )
            locals_to_review.append(current["primary_local_id"])
        for local_id in locals_to_review:
            set_review_label(
                conn,
                local_id,
                label=label,
                notes=notes,
                reviewer_email=reviewer_email.strip(),
                commit=False,
            )
    return {
        "reviewed": len(selection),
        "label": label,
        "candidate_ids": [r["candidate_id"] for r in selection],
    }
