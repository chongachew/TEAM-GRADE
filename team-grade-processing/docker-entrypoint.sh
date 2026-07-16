#!/bin/sh
# Writes the GCP service-account JSON out to disk from an env var at
# container startup, instead of ever baking the credential file into the
# image - this exact credential type already leaked once from this project
# (2026-07-08 cleanup) and must never be a COPY'd file or an image layer
# again. In ECS this env var is injected from Secrets Manager.
set -e

if [ -n "$GOOGLE_APPLICATION_CREDENTIALS_JSON" ]; then
  # Two locations, not one: ingest/firestore_client.py and config/settings.py
  # independently compute different absolute paths for the "same" credentials
  # file (3 vs 2 parent-dir hops from their own location) - a pre-existing
  # inconsistency in the app code, not something this image should need to
  # fix to just start up. /credentials is what firestore_client.py (the
  # actual runtime Firestore connection) resolves to given this image's
  # flattened /app layout; /app/credentials is what settings.py resolves to.
  mkdir -p /credentials /app/credentials
  printf '%s' "$GOOGLE_APPLICATION_CREDENTIALS_JSON" > /credentials/service-account.json
  printf '%s' "$GOOGLE_APPLICATION_CREDENTIALS_JSON" > /app/credentials/service-account.json
  export GOOGLE_APPLICATION_CREDENTIALS=/credentials/service-account.json
fi

exec "$@"
