# Bridge Athletics — System Architecture

## Overview

The system consists of:

1. Rivals Scraper
2. High School Normalizer
3. YouTube Query Generator
4. YouTube Metadata Crawler
5. Deduplication Engine
6. Local Worker (download + normalization)
7. Analysis Pipeline (detection → tracking → pose → traits)
8. Competition Tier Classifier

## Data Flow

Rivals → High Schools → YouTube → Firestore → Local Worker → Normalized Film → Analysis → Competition Tier