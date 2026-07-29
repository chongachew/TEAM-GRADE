"""
Tests for classical (color + texture) referee-uniform classification.
"""

import numpy as np
import cv2

from processing.uniform_classifier import (
    ROLE_PLAYER,
    ROLE_REFEREE,
    ROLE_UNCERTAIN,
    classify_uniform,
)


def _striped_image(h=160, w=160, stripe_width=12, dark=20, light=240):
    """A vertical black/white striped pattern - referee uniform stand-in."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for x in range(w):
        value = dark if (x // stripe_width) % 2 == 0 else light
        img[:, x] = (value, value, value)
    return img


def _solid_image(h=160, w=160, bgr=(255, 255, 255)):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = bgr
    return img


def test_classify_uniform_detects_real_stripe_pattern():
    role, confidence = classify_uniform(_striped_image())
    assert role == ROLE_REFEREE
    assert confidence > 0.5


def test_classify_uniform_solid_white_is_not_referee():
    role, confidence = classify_uniform(_solid_image(bgr=(255, 255, 255)))
    assert role == ROLE_PLAYER


def test_classify_uniform_solid_black_is_not_referee():
    role, confidence = classify_uniform(_solid_image(bgr=(10, 10, 10)))
    assert role == ROLE_PLAYER


def test_classify_uniform_solid_team_color_is_player():
    # Solid saturated green jersey - real team color, not black/white.
    role, confidence = classify_uniform(_solid_image(bgr=(40, 180, 40)))
    assert role == ROLE_PLAYER


def test_classify_uniform_tiny_crop_is_uncertain():
    role, confidence = classify_uniform(np.zeros((5, 5, 3), dtype=np.uint8))
    assert role == ROLE_UNCERTAIN
    assert confidence == 0.0


def test_classify_uniform_noisy_grayscale_is_not_referee():
    # Grayscale but random noise, not a repeating pattern - a solid
    # gray/black-and-white garment with lighting noise, not real stripes.
    rng = np.random.default_rng(7)
    gray = rng.integers(100, 140, (160, 160), dtype=np.uint8)  # narrow mid-tone band only
    img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    role, confidence = classify_uniform(img)
    assert role == ROLE_PLAYER
