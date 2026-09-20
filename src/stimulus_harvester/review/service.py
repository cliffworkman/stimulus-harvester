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
