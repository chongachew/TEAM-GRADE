# fixtures/README.md
# Test fixtures for the pre-ingestion segmentation pipeline.
#
# Place a short (≈10-second) sample video here as sample_10sec.mp4 for
# integration tests.  The file is excluded from version control via .gitignore
# to keep repository size small.
#
# To generate a synthetic test video:
#   ffmpeg -f lavfi -i testsrc=duration=10:size=854x480:rate=30 \
#          -f lavfi -i sine=frequency=3000:duration=10 \
#          -c:v libx264 -preset ultrafast -c:a aac \
#          tests/fixtures/sample_10sec.mp4
