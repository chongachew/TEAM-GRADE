"""
Uniform Classifier
Classical (no neural net) classification of a torso crop as "referee" -
black/white striped uniform, distinct from either team's colors - vs
"player". Intended to run against the tight, pose-keypoint-driven torso crop
`torso_crop_stage.py`/`processing/torso_cropper.py` already produce for
jersey OCR.

Design history (two real-data rounds, not just synthetic):
- Round 1 tested a single whole-box color-fraction check against real
  detection-bbox crops (the loose full-person box) and it broke two ways: a
  solid white player jersey read as "referee-like" (plain white and a
  black/white stripe pattern both look "grayscale, bright" as a single
  fraction), and a real referee's crop read as mostly green because the
  loose bbox included background grass, diluting the actual stripe signal.
- Round 2 replaced the color fraction with a stripe/texture check (row-mean
  sign-alternation) but tested it against real crops (both loose detection
  boxes AND real torso_crop_stage output pulled from rRDZymlc8aI) and found
  it's still fooled two ways: (a) a real referee's full-body crop is mostly
  non-striped area (head, pants, shoes) so a signal averaged/medianed across
  the WHOLE crop gets diluted below any reasonable threshold, and (b) a
  loose or multi-subject crop's background/logo/fold noise produces enough
  spurious sign-alternations around a row's mean to look "striped" even on a
  solid jersey. Real `torso_crop_stage` output for this video also turned
  out to sometimes include more than one person (a pose/track-matching
  issue in dense formations, not something this module can fix) - the same
  problem in a different guise.

Fixed by: (a) restricting analysis to the CENTER of the crop - the same
technique `scripts/pixel_motion_fallback.py` already uses elsewhere in this
codebase for an analogous reason ("close-ups are framed on the subject -
center-weighting avoids picking up crowd/sideline motion at the edges");
here it avoids a second person or background at the crop's margins. (b)
Replacing "count sign-changes around the row mean" with a dark-band/
light-band check that ignores mid-tone pixels entirely (shadows, fabric
folds, JPEG noise) and only counts a row as "striped" if it has real
presence in BOTH a genuinely dark band and a genuinely light band. (c)
Aggregating over the fraction of sampled rows that qualify as striped,
not requiring the whole crop - a real striped shirt only occupies part of
even a torso-tight crop's height once head/pants are included.
"""

import logging
from typing import Tuple

import cv2
import numpy as np

from config import settings

logger = logging.getLogger(__name__)

ROLE_REFEREE = "referee"
ROLE_PLAYER = "player"
ROLE_UNCERTAIN = "uncertain"

_ROW_STRIDE = 3


def classify_uniform(torso_crop_bgr: np.ndarray) -> Tuple[str, float]:
    """Classify a single torso crop as referee/player/uncertain.

    Returns (role, confidence). Thresholds are calibrated against real
    crops pulled from rRDZymlc8aI (both loose detection-bbox crops and real
    torso_crop_stage output - see module docstring and the plan file) but
    remain placeholders pending a larger real-data pass once
    ROLE_CLASSIFICATION_ENABLED has run against more than one video.
    """
    h, w = torso_crop_bgr.shape[:2]
    if h < settings.UNIFORM_CLASSIFIER_MIN_CROP_SIZE_PX or w < settings.UNIFORM_CLASSIFIER_MIN_CROP_SIZE_PX:
        return ROLE_UNCERTAIN, 0.0

    frac = settings.UNIFORM_CLASSIFIER_CENTER_CROP_FRAC
    cy0, cy1 = int(h * (1 - frac) / 2), int(h * (1 + frac) / 2)
    cx0, cx1 = int(w * (1 - frac) / 2), int(w * (1 + frac) / 2)
    center = torso_crop_bgr[cy0:cy1, cx0:cx1]
    if center.size == 0:
        return ROLE_UNCERTAIN, 0.0

    hsv = cv2.cvtColor(center, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    low_sat_mask = saturation < settings.UNIFORM_CLASSIFIER_SAT_THRESHOLD
    low_sat_frac = float(np.mean(low_sat_mask))

    if low_sat_frac < settings.UNIFORM_CLASSIFIER_MIN_LOW_SAT_FRAC:
        # Enough real color (team jersey, jacket) that this isn't a
        # black/white uniform at all - confident it's not a referee without
        # needing to look for a stripe pattern.
        confidence = min(0.95, 0.5 + (settings.UNIFORM_CLASSIFIER_MIN_LOW_SAT_FRAC - low_sat_frac))
        return ROLE_PLAYER, confidence

    striped_row_frac = _striped_row_fraction(center)
    threshold = settings.UNIFORM_CLASSIFIER_MIN_STRIPED_ROW_FRAC
    if striped_row_frac >= threshold:
        confidence = min(0.95, 0.5 + (striped_row_frac - threshold))
        return ROLE_REFEREE, confidence

    confidence = min(0.95, 0.5 + (threshold - striped_row_frac))
    return ROLE_PLAYER, confidence


def _striped_row_fraction(crop_bgr: np.ndarray) -> float:
    """Fraction of sampled rows that show a genuine alternating dark/light
    pattern when scanned left-to-right (referee stripes run vertically -
    alternating left-to-right across the shirt - so the pattern shows up as
    horizontal alternation within each row, not down each column).

    A row only counts as "striped" if it has real presence in both a dark
    band and a light band (ignoring mid-tones - shadow/fold/JPEG noise
    lives there, not the stripe signal) AND alternates between those bands
    more than once. A solid jersey with folds/shadows might dip into one
    band but rarely alternates back and forth repeatedly; real stripes do.
    """
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    h = gray.shape[0]

    dark_thresh = settings.UNIFORM_CLASSIFIER_DARK_THRESHOLD
    light_thresh = settings.UNIFORM_CLASSIFIER_LIGHT_THRESHOLD
    min_band_frac = settings.UNIFORM_CLASSIFIER_MIN_BAND_FRAC
    min_transitions = settings.UNIFORM_CLASSIFIER_MIN_BAND_TRANSITIONS

    striped_rows = 0
    sampled_rows = 0
    for y in range(0, h, _ROW_STRIDE):
        row = gray[y]
        sampled_rows += 1

        dark_frac = float(np.mean(row < dark_thresh))
        light_frac = float(np.mean(row > light_thresh))
        if dark_frac < min_band_frac or light_frac < min_band_frac:
            continue

        # Label each pixel dark(-1)/light(+1)/mid(0), then count
        # transitions between consecutive non-mid labels only - mid-tone
        # runs (shadow gradients, JPEG ringing) are skipped rather than
        # treated as their own state, so they can't inflate the count.
        labels = np.zeros(row.shape, dtype=np.int8)
        labels[row < dark_thresh] = -1
        labels[row > light_thresh] = 1
        non_mid = labels[labels != 0]
        if non_mid.size < 2:
            continue
        transitions = int(np.sum(non_mid[1:] != non_mid[:-1]))

        if transitions >= min_transitions:
            striped_rows += 1

    if sampled_rows == 0:
        return 0.0
    return striped_rows / sampled_rows
