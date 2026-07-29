"""
Tests for classical (color + line) field-boundary detection.
"""

import numpy as np
import cv2

from processing.field_boundary import (
    point_in_field,
    refine_boundary_with_lines,
    segment_field_polygon,
)


def _rect_polygon(x1, y1, x2, y2):
    return np.array(
        [[[x1, y1]], [[x2, y1]], [[x2, y2]], [[x1, y2]]],
        dtype=np.int32,
    )


def test_segment_field_polygon_finds_green_rectangle():
    canvas = np.full((400, 600, 3), (60, 60, 60), dtype=np.uint8)  # gray "crowd" background
    cv2.rectangle(canvas, (100, 100), (500, 350), (40, 180, 40), thickness=-1)  # BGR green field

    polygon = segment_field_polygon(canvas)

    assert polygon is not None
    x, y, w, h = cv2.boundingRect(polygon)
    # Bounding box should roughly match the drawn rectangle (some slack for
    # the morphological close/open passes).
    assert abs(x - 100) < 15
    assert abs(y - 100) < 15
    assert abs((x + w) - 500) < 15
    assert abs((y + h) - 350) < 15


def test_segment_field_polygon_returns_none_on_blank_frame():
    canvas = np.zeros((200, 200, 3), dtype=np.uint8)
    assert segment_field_polygon(canvas) is None


def test_segment_field_polygon_ignores_disconnected_green_blob():
    canvas = np.full((400, 600, 3), (60, 60, 60), dtype=np.uint8)
    cv2.rectangle(canvas, (100, 100), (500, 350), (40, 180, 40), thickness=-1)  # main field
    cv2.rectangle(canvas, (0, 0), (30, 30), (40, 180, 40), thickness=-1)  # small, separate green patch

    polygon = segment_field_polygon(canvas)

    assert polygon is not None
    x, y, w, h = cv2.boundingRect(polygon)
    # The small disconnected patch at the origin must not pull the bounding
    # box's top-left corner toward (0, 0) - the main field is the much
    # larger connected component and should win outright.
    assert x > 50
    assert y > 50


def test_point_in_field():
    polygon = _rect_polygon(100, 100, 500, 350)
    assert point_in_field(300, 200, polygon) is True
    assert point_in_field(5, 5, polygon) is False


def test_refine_boundary_with_lines_snaps_toward_real_line():
    canvas = np.full((400, 600, 3), (60, 60, 60), dtype=np.uint8)
    # The real sideline: a white line at y=150.
    cv2.line(canvas, (50, 150), (550, 150), (255, 255, 255), thickness=3)
    # An unrelated white line far from the coarse polygon's band - must be
    # ignored (mirrors the real background-structure false positive found
    # against real footage before this band-constraint was added).
    cv2.line(canvas, (580, 0), (580, 50), (255, 255, 255), thickness=3)

    # A deliberately coarse polygon whose top edge (y=160) sits a few pixels
    # below the real line (y=150) - simulates the green blob's edge not
    # exactly matching the painted line.
    coarse = _rect_polygon(60, 160, 540, 300)

    refined = refine_boundary_with_lines(canvas, coarse, band_px=22)

    top_ys = refined.reshape(-1, 2)[:, 1]
    # The two top vertices should have moved up, toward y=150.
    assert min(top_ys) < 155
    # The bottom edge (no nearby line) should be unchanged.
    assert max(refined.reshape(-1, 2)[:, 1]) == 300


def test_refine_boundary_with_lines_falls_back_when_no_lines_found():
    canvas = np.full((400, 600, 3), (60, 60, 60), dtype=np.uint8)  # no white lines at all
    coarse = _rect_polygon(60, 160, 540, 300)

    refined = refine_boundary_with_lines(canvas, coarse, band_px=22)

    assert np.array_equal(refined, coarse)
