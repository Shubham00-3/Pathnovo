from __future__ import annotations

import cv2
import numpy as np

from delta_chat.delta.visual_diff import residual_geometry_changes


def _config() -> dict:
    return {
        "visual_diff": {
            "enabled": True,
            "min_component_area": 20,
            "max_components": 10,
            "residual_threshold": 20,
            "suppress_border_ratio": 0.02,
            "max_emit": 10,
        }
    }


def test_black_ink_added_to_revision_b_has_added_direction(tmp_path) -> None:
    before = np.full((160, 160), 255, dtype=np.uint8)
    after = before.copy()
    cv2.rectangle(after, (60, 60), (100, 100), 0, thickness=-1)
    path_a = tmp_path / "a.png"
    path_b = tmp_path / "b.png"
    cv2.imwrite(str(path_a), before)
    cv2.imwrite(str(path_b), after)

    changes = residual_geometry_changes(
        path_a,
        path_b,
        pixel_matrix=[[1, 0, 0], [0, 1, 0]],
        config=_config(),
        pid_b="PID-B",
        page_number=1,
        existing_boxes=[],
    )

    assert changes
    assert changes[0]["change_type"] == "added"


def test_black_ink_removed_from_revision_b_has_removed_direction(tmp_path) -> None:
    before = np.full((160, 160), 255, dtype=np.uint8)
    cv2.rectangle(before, (60, 60), (100, 100), 0, thickness=-1)
    after = np.full((160, 160), 255, dtype=np.uint8)
    path_a = tmp_path / "a.png"
    path_b = tmp_path / "b.png"
    cv2.imwrite(str(path_a), before)
    cv2.imwrite(str(path_b), after)

    changes = residual_geometry_changes(
        path_a,
        path_b,
        pixel_matrix=[[1, 0, 0], [0, 1, 0]],
        config=_config(),
        pid_b="PID-B",
        page_number=1,
        existing_boxes=[],
    )

    assert changes
    assert changes[0]["change_type"] == "removed"
