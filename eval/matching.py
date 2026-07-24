"""Bipartite matching of predicted deltas to ground truth."""

from __future__ import annotations

from typing import Any

import numpy as np
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

from delta_chat.canonical.coordinates import bbox_iou, centroid_distance


def _pred_list(delta: dict) -> list[dict]:
    return list(delta.get("changes") or [])


def match_predictions_to_gt(
    predictions: list[dict],
    ground_truth: list[dict],
    *,
    centroid_tol: float = 0.08,
    iou_thr: float = 0.05,
) -> dict[str, Any]:
    if not ground_truth and not predictions:
        return {
            "tp": 0,
            "fp": 0,
            "fn": 0,
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "pairs": [],
            "false_positives": [],
            "false_negatives": [],
        }
    if not ground_truth:
        return {
            "tp": 0,
            "fp": len(predictions),
            "fn": 0,
            "precision": 0.0,
            "recall": 1.0,
            "f1": 0.0,
            "pairs": [],
            "false_positives": predictions,
            "false_negatives": [],
        }
    if not predictions:
        return {
            "tp": 0,
            "fp": 0,
            "fn": len(ground_truth),
            "precision": 1.0 if not ground_truth else 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "pairs": [],
            "false_positives": [],
            "false_negatives": ground_truth,
        }

    n, m = len(predictions), len(ground_truth)
    cost = np.ones((n, m), dtype=np.float64)
    for i, p in enumerate(predictions):
        pb = (p.get("region") or {}).get("bbox") or [0, 0, 0, 0]
        for j, g in enumerate(ground_truth):
            if p.get("change_type") != g.get("change_type"):
                continue
            # entity type soft
            et_ok = True
            if g.get("entity_type") and p.get("entity_type"):
                if g["entity_type"] not in {
                    p["entity_type"],
                    "geometry_region",
                    "text",
                    "instrument_tag",
                    "table_cell",
                    "note",
                    "line_tag",
                } and p["entity_type"] not in {
                    g["entity_type"],
                    "geometry_region",
                    "text",
                    "geometry_cluster",
                }:
                    # allow loose match for text-ish
                    if g["entity_type"] not in {
                        "instrument_tag",
                        "table_cell",
                        "note",
                        "line_tag",
                        "geometry_region",
                    }:
                        et_ok = False
            if not et_ok:
                continue
            score = 0.4
            # text agreement
            for key_p, key_g in (("before", "before"), ("after", "after")):
                pv, gv = p.get(key_p), g.get(key_g)
                if pv and gv:
                    score += 0.25 * (fuzz.partial_ratio(str(pv), str(gv)) / 100.0)
                elif pv is None and gv is None:
                    score += 0.1
            gb = g.get("bbox") or [0, 0, 0, 0]
            if gb and pb and any(gb):
                iou = bbox_iou(pb, gb)
                dist = centroid_distance(pb, gb)
                if iou >= iou_thr or dist <= centroid_tol:
                    score += 0.3 * max(iou, 1.0 - dist / max(centroid_tol, 1e-6))
                else:
                    score *= 0.5
            cost[i, j] = 1.0 - min(1.0, score)

    ri, cj = linear_sum_assignment(cost)
    pairs = []
    used_p, used_g = set(), set()
    for i, j in zip(ri, cj, strict=False):
        s = 1.0 - float(cost[i, j])
        if s < 0.45:
            continue
        pairs.append(
            {
                "pred_index": i,
                "gt_index": j,
                "score": s,
                "pred": predictions[i],
                "gt": ground_truth[j],
            }
        )
        used_p.add(i)
        used_g.add(j)

    tp = len(pairs)
    fp = [predictions[i] for i in range(n) if i not in used_p]
    fn = [ground_truth[j] for j in range(m) if j not in used_g]
    precision = tp / (tp + len(fp)) if (tp + len(fp)) else 0.0
    recall = tp / (tp + len(fn)) if (tp + len(fn)) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "tp": tp,
        "fp": len(fp),
        "fn": len(fn),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "pairs": pairs,
        "false_positives": fp,
        "false_negatives": fn,
    }
