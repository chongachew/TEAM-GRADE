"""
Field Boundary Detection
Classical (no neural net) detection of the playing field's boundary polygon,
used to filter out crowd/sideline/background-structure detections without
needing roster fine-tuning. Two-stage: a green-turf color segmentation gives
a reliable base region (validated against real broadcast footage - crowd
stands, team benches, and a disconnected background practice field are all
correctly excluded, since they're either the wrong color or a separate,
smaller connected component); an optional white-line refinement pass then
tightens that base polygon toward the field's own painted sideline, using a
thin band around the polygon's own contour to reject unrelated bright
background structures (a stadium roofline, a background field) that a
naive/unconstrained white-pixel search picks up just as often as the real
sideline - both rounds validated against real rRDZymlc8aI frames, see the
plan file for the concrete before/after.

Does NOT distinguish players from referees standing on the field - that's a
separate uniform-color classification (processing/uniform_classifier.py).
This only separates "on the field" from "not on the field".
"""

import logging
from typing import List, Optional, Tuple

import cv2
import numpy as np

from config import settings

logger = logging.getLogger(__name__)

# HSV green range for turf - calibrated against real rRDZymlc8aI frames
# (natural grass, daylight broadcast footage). Revisit if used against
# artificial turf or very different lighting/time-of-day footage.
_GREEN_LOWER = np.array([
    settings.FIELD_BOUNDARY_GREEN_HUE_MIN,
    settings.FIELD_BOUNDARY_GREEN_SAT_MIN,
    settings.FIELD_BOUNDARY_GREEN_VAL_MIN,
])
_GREEN_UPPER = np.array([settings.FIELD_BOUNDARY_GREEN_HUE_MAX, 255, 255])

# Morphology: close bridges gaps left by players/paint lines interrupting the
# green blob; open drops small disconnected noise (e.g. background trees).
_CLOSE_KERNEL = np.ones((25, 25), np.uint8)
_OPEN_KERNEL = np.ones((9, 9), np.uint8)

# White-paint range for the line-refinement pass - low saturation, high value.
_WHITE_LOWER = np.array([0, 0, 170])
_WHITE_UPPER = np.array([180, 60, 255])

# A detected line must run within this many degrees of a polygon edge's own
# direction to be treated as a refinement of that edge, not noise. A line
# segment's direction is ambiguous by 180 degrees, so this is compared via
# absolute dot product between unit direction vectors.
_MAX_ANGLE_DIFF_DEG = 20.0


def segment_field_polygon(frame_bgr: np.ndarray) -> Optional[np.ndarray]:
    """Segment the playing field via green-turf color.

    Returns the field's convex hull (cv2 contour format: Nx1x2 int32) or
    None if no plausible field region was found (degenerate/corrupted
    frame) - callers must fail open (keep all detections for that frame)
    rather than treating None as "field covers nothing."
    """
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, _GREEN_LOWER, _GREEN_UPPER)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _CLOSE_KERNEL)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _OPEN_KERNEL)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < settings.FIELD_BOUNDARY_MIN_AREA_PX:
        return None

    return cv2.convexHull(largest)


def point_in_field(x: float, y: float, field_polygon: np.ndarray) -> bool:
    """True if (x, y) falls inside or on the boundary of field_polygon."""
    return cv2.pointPolygonTest(field_polygon, (float(x), float(y)), False) >= 0


def refine_boundary_with_lines(
    frame_bgr: np.ndarray,
    field_polygon: np.ndarray,
    band_px: Optional[int] = None,
) -> np.ndarray:
    """Tighten field_polygon toward the real painted sideline.

    Deliberately does not search the whole frame for white pixels - a naive
    whole-frame search finds real sideline paint in some shots but false-
    positives on unrelated bright background structures (a stadium roofline,
    an adjacent practice field) just as often, since those are also bright/
    low-saturation. Constraining the search to a band around this exact
    polygon's own edge rejects those (validated against real broadcast
    footage - see the plan file for both rounds' results).

    Falls back to the unrefined polygon (or individual unchanged edges)
    wherever no matching line is found nearby - never expands/shrinks the
    boundary without real line evidence.
    """
    if band_px is None:
        band_px = settings.FIELD_BOUNDARY_LINE_BAND_PX

    h, w = frame_bgr.shape[:2]
    band_mask = np.zeros((h, w), dtype=np.uint8)
    cv2.drawContours(band_mask, [field_polygon], -1, 255, thickness=band_px * 2)

    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    white_mask = cv2.inRange(hsv, _WHITE_LOWER, _WHITE_UPPER)
    band_white = cv2.bitwise_and(white_mask, band_mask)
    band_white = cv2.morphologyEx(band_white, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    edges = cv2.Canny(band_white, 50, 150)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=settings.FIELD_BOUNDARY_HOUGH_THRESHOLD,
        minLineLength=int(w * settings.FIELD_BOUNDARY_HOUGH_MIN_LINE_LENGTH_FRAC),
        maxLineGap=25,
    )
    if lines is None:
        return field_polygon

    return _snap_polygon_to_lines(field_polygon, lines, band_px)


def _snap_polygon_to_lines(polygon: np.ndarray, lines: np.ndarray, band_px: int) -> np.ndarray:
    """For each polygon edge, if detected line segments run roughly
    parallel to it, sit within band_px, and agree with each other, nudge
    that edge to their average perpendicular offset. Edges with no
    confident nearby match are left unchanged.

    Offsets are computed against the ORIGINAL vertex positions and applied
    in one pass at the end (accumulated per-vertex as an average, not a
    running sum) so that a vertex shared by two matched adjacent edges
    doesn't get shifted twice.
    """
    pts = polygon.reshape(-1, 2).astype(np.float64)
    n = len(pts)
    edge_shift: List[Optional[np.ndarray]] = [None] * n

    for i in range(n):
        p1, p2 = pts[i], pts[(i + 1) % n]
        edge_vec = p2 - p1
        edge_len = float(np.linalg.norm(edge_vec))
        if edge_len < 1e-6:
            continue
        edge_dir = edge_vec / edge_len
        normal = np.array([-edge_dir[1], edge_dir[0]])

        offsets = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            line_vec = np.array([x2 - x1, y2 - y1], dtype=np.float64)
            line_len = float(np.linalg.norm(line_vec))
            if line_len < 1e-6:
                continue
            line_dir = line_vec / line_len

            if abs(np.dot(edge_dir, line_dir)) < np.cos(np.radians(_MAX_ANGLE_DIFF_DEG)):
                continue

            mid = np.array([(x1 + x2) / 2.0, (y1 + y2) / 2.0])
            to_mid = mid - p1
            along = np.dot(to_mid, edge_dir)
            # Only count segments that actually project onto this edge's own
            # span - otherwise a line matching this edge's direction but
            # sitting near a different edge could get misattributed.
            if not (0.0 <= along <= edge_len):
                continue
            perp_dist = np.dot(to_mid, normal)
            if abs(perp_dist) > band_px:
                continue
            offsets.append(perp_dist)

        if not offsets:
            continue
        # Disagreeing offsets mean this is probably noise, not a real
        # boundary correction - fail open on that edge.
        if np.std(offsets) > band_px / 2:
            continue

        edge_shift[i] = normal * float(np.mean(offsets))

    vertex_shifts: List[List[np.ndarray]] = [[] for _ in range(n)]
    for i, shift in enumerate(edge_shift):
        if shift is None:
            continue
        vertex_shifts[i].append(shift)
        vertex_shifts[(i + 1) % n].append(shift)

    new_pts = pts.copy()
    for i, shifts in enumerate(vertex_shifts):
        if shifts:
            new_pts[i] += np.mean(shifts, axis=0)

    return new_pts.astype(np.int32).reshape(-1, 1, 2)
