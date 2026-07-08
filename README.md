# TEAM-GRADE
AI Game footage grader for college football scouting

**Status Badges:**
[![Python Tests](https://github.com/chongachew/TEAM-GRADE/actions/workflows/python-tests.yml/badge.svg?branch=main)](https://github.com/chongachew/TEAM-GRADE/actions/workflows/python-tests.yml)
[![Node.js Tests](https://github.com/chongachew/TEAM-GRADE/actions/workflows/node-tests.yml/badge.svg?branch=main)](https://github.com/chongachew/TEAM-GRADE/actions/workflows/node-tests.yml)
[![Code Quality](https://github.com/chongachew/TEAM-GRADE/actions/workflows/code-quality.yml/badge.svg?branch=main)](https://github.com/chongachew/TEAM-GRADE/actions/workflows/code-quality.yml)
[![Build & Deploy](https://github.com/chongachew/TEAM-GRADE/actions/workflows/build-deploy.yml/badge.svg?branch=main)](https://github.com/chongachew/TEAM-GRADE/actions/workflows/build-deploy.yml)
[![Integration Tests](https://github.com/chongachew/TEAM-GRADE/actions/workflows/integration-tests.yml/badge.svg?branch=main)](https://github.com/chongachew/TEAM-GRADE/actions/workflows/integration-tests.yml)

---

# Bridge Athletics — Film Ingestion & Analysis Pipeline

This repository contains the full ingestion and analysis system for Bridge Athletics:
- YouTube → Firestore → Local Worker → Normalized Film
- Player detection, tracking, pose estimation
- Scouting trait extraction based on coaching manuals
- Competition-tier classification using offer data + film features

## Folder Structure

```
bridge-athletics/
│
├── ingestion/              # Download + normalization worker
│   ├── local_worker.py
│   ├── firestore_utils.py
│   ├── raw/
│   └── normalized/
│
├── analysis/               # ML + feature extraction
│   ├── trait_dictionary.json
│   ├── feature_extraction/
│   ├── competition_model/
│   └── utils/
│
├── firestore/              # Firestore schema + examples
│   ├── schema.md
│   └── examples/
│
└── docs/                   # Architecture + philosophy
    ├── architecture.md
    ├── pipeline_overview.md
    └── scouting_philosophy.md
```

## Overview

This system ingests high school football games from YouTube, normalizes them, extracts player traits, and predicts competition level using a combination of:
- Computer vision
- Tracking
- Pose estimation
- Offer data
- Scouting trait dictionaries

## Status

Early development — ingestion pipeline first, analysis pipeline next.

## CI/CD & Testing

This project includes comprehensive automated testing and deployment workflows via GitHub Actions:

**Available Workflows:**
- ✅ **Python Tests** - Unit tests across Python 3.9-3.12 on Linux/Windows/macOS
- ✅ **Node.js Tests** - Frontend tests across Node 16-20 on all platforms
- ✅ **Code Quality** - Linting, formatting, security scanning (Pylint, Flake8, Bandit)
- ✅ **Build & Deploy** - Automated build verification and staging deployment
- ✅ **Integration Tests** - End-to-end tests with Firestore emulator

**Current Test Status:**
- 68/68 unit tests passing ✅
- Coverage reports available in Codecov
- Code quality checks on every PR

**Getting Started with Tests:**
```bash
# Run all tests locally
cd team-grade-processing
pytest tests/test_endpoints_simplified.py tests/test_exceptions.py tests/test_validators.py -v

# Run with coverage
pytest tests/ --cov=api --cov=ingest --cov-report=html
```

See [CI_CD_SETUP_GUIDE.md](./CI_CD_SETUP_GUIDE.md) for detailed documentation on:
- How to configure optional services (SonarCloud, Slack, Codecov)
- How to add the status badges to your README
- Workflow customization for your deployment platform
- Security best practices for CI/CD

---