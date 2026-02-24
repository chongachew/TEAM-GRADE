# TEAM-GRADE
AI Game footage grader for college football scouting

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