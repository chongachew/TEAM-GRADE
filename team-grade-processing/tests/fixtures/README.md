# Test Fixtures

## `vtest_multiplayer_standin.avi`

Placeholder multi-person clip for smoke-testing the detection/tracking pipeline
only - **not representative of actual football broadcast footage** TEAM-GRADE
targets. Replace once real multi-athlete clips are available (see plan Phase 2).

- Source: OpenCV's own official sample data (`samples/data/vtest.avi` in the
  [opencv/opencv](https://github.com/opencv/opencv) repository), BSD-licensed,
  ubiquitous in computer-vision background-subtraction/pedestrian-tracking
  tutorials. Pedestrians walking on a street - 795 frames @ 10fps, 768x576.
- Verified (2026-07-08) to contain multiple real, confidently-detectable people
  via RF-DETR (3 persons detected at 0.67-0.91 confidence on a mid-video frame)
  - a genuine multi-object scene, appropriate for smoke-testing detection,
    tracking through occlusion, and identity re-association, even though it
    isn't football footage.
- Not committed to git (binary, `.gitignore`'d under the general video-data
  patterns) - re-download via:
  ```bash
  curl -L -o tests/fixtures/vtest_multiplayer_standin.avi \
    https://raw.githubusercontent.com/opencv/opencv/master/samples/data/vtest.avi
  ```
