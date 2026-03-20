# Trench Engine - Football Analytics Engine

## Overview

The **Trench Engine** is a modular, config-driven football analytics system that converts pose/tracking data into:
- Biomechanical features
- Trait scores (0–100)
- Composite grades
- Rep → Play → Game → Season outputs

## Architecture

### Core Principles

✓ **Position-Agnostic** - Supports any position via configuration
✓ **Config-Driven** - No hard-coded thresholds or logic
✓ **Extensible** - Add new positions, traits, and features easily
✓ **Level-Aware** - HS/college/pro overrides and adjustments
✓ **Program-Aware** - Scheme/team-specific configuration

### Directory Structure

```
trench_engine/
├── config/                    # Configuration files
│   ├── base/                 # Universal defaults
│   │   └── universal.json
│   ├── levels/               # Level overrides (hs/college/pro)
│   ├── programs/             # Program/scheme overrides
│   ├── positions/            # Position-specific configs
│   │   ├── ol.json
│   │   ├── dl.json
│   │   ├── qb.json
│   │   ├── rb.json
│   │   ├── wr.json
│   │   ├── db.json
│   │   └── lb.json
│   └── config_loader.py      # Configuration management
│
├── pipeline/                  # Core processing pipeline
│   └── pipeline.py           # 7-stage pipeline orchestrator
│
├── scoring/                   # Trait scoring logic
│   └── trait_scorer.py       # Score bucketing and grading
│
├── aggregation/              # Aggregation logic
│   └── aggregator.py         # Rep/Play/Game/Season aggregation
│
├── engine.py                 # Main engine orchestrator
├── examples.py               # Usage examples
└── README.md                 # This file
```

## Pipeline Stages

The Trench Engine processes data through 7 sequential stages:

### 1. Pose Estimation
Convert raw motion capture/video to keypoint data

### 2. Pose Normalization
Normalize keypoints relative to player body frame

### 3. Event Segmentation
Identify discrete events (plant, push-off, contact, etc.)

### 4. Feature Extraction
Extract biomechanical features using position-specific extractors

### 5. Trait Scoring
Convert features to trait scores (0-100) using weighted aggregation

### 6. Aggregation
Combine rep → play → game → season levels

### 7. Output Formatting
Generate structured outputs at each aggregation level

## Configuration

### Config Load Order

Configurations are merged in this order (later overrides earlier):

1. **Base** - `config/base/universal.json` (global defaults)
2. **Level** - `config/levels/{level}.json` (hs/college/pro)
3. **Program** - `config/programs/{program}.json` (team/scheme)
4. **Position** - `config/positions/{position}.json` (position-specific)

### Position Config Structure

Each position config defines:

```json
{
  "position": "OL",
  "name": "Offensive Line",
  "features": {
    "category": [
      {
        "name": "featureName",
        "source": "pose_estimation|tracking_data|biomechanics|video_analysis",
        "description": "...",
        "type": "numeric|categorical",
        "normalization": "percentile|standard"
      }
    ]
  },
  "traits": {
    "traitName": {
      "features": ["feature1", "feature2"],
      "weights": [0.6, 0.4],
      "description": "..."
    }
  },
  "weights": {
    "category1": 0.5,
    "category2": 0.5
  }
}
```

## Usage

### Basic Example

```python
from trench_engine import TrenchEngine

# Initialize engine
engine = TrenchEngine("path/to/config")

# Process a single rep
rep = engine.process_player_rep(
    position="QB",
    player_id="QB001",
    pose_data={...},
    tracking_data={...},
    timestamp=0.5,
    level="college"
)

# Access features and traits
print(f"Features: {rep.features}")
print(f"Traits: {rep.traits}")
```

### Aggregation Workflow

```python
# Process multiple reps into a play
play = engine.process_player_play(
    position="QB",
    player_id="QB001",
    play_id="PLAY_001",
    reps=[rep1, rep2, rep3, ...]
)

# Process plays into a game
game = engine.process_player_game(
    position="QB",
    player_id="QB001",
    game_id="GAME_001",
    plays=[play1, play2, ...]
)

# Process games into a season
season = engine.process_player_season(
    position="QB",
    player_id="QB001",
    season=2024,
    games=[game1, game2, ...]
)

# Generate summary
summary = engine.get_player_summary("QB", "QB001", season)
```

### Player Comparison

```python
# Compare players at a position
comparison = engine.compare_players(
    position="WR",
    players={
        "WR001": season_data_1,
        "WR002": season_data_2,
        "WR003": season_data_3
    }
)
```

## Position Modules

### OL (Offensive Line)
- **Features**: Pass protection, run blocking, hand usage, movement
- **Traits**: Anchor, hand placement, strike timing, run blocking

### DL (Defensive Line)
- **Features**: Get off, pass rush, power, hand usage
- **Traits**: Get off, penetration, hand usage, rush effectiveness

### QB (Quarterback)
- **Features**: Mechanics, arm talent, accuracy, processing
- **Traits**: Release mechanics, sequencing, progression timing, ball placement

### RB (Running Back)
- **Features**: Vision, burst, balance, elusiveness
- **Traits**: Burst, vision, balance, elusiveness

### WR (Wide Receiver)
- **Features**: Release, hands, route running
- **Traits**: Release, hands, route running

### DB (Defensive Back)
- **Features**: Backpedal, man coverage, zone coverage, ball skills
- **Traits**: Press technique, transitions, coverage, ball skills

### LB (Linebacker)
- **Features**: Run fits, pursuit, block shedding, coverage
- **Traits**: Run fits, pursuit, block deconstruction, coverage

## Scoring System

### Score Buckets

Numeric scores are classified into 5 buckets:
- **Elite**: 90-100
- **High**: 75-89
- **Average**: 50-74
- **Below Average**: 25-49
- **Poor**: 0-24

### Grade Conversion

Numeric scores → Letter grades:
- A: 90+
- B: 80-89
- C: 70-79
- D: 60-69
- F: <60

### Trait Scoring Formula

$$\text{Trait Score} = \sum_{i=1}^{n} \text{Feature}_i \times \text{Weight}_i$$

### Composite Grade

$$\text{Grade} = \frac{\sum_{i=1}^{m} \text{Trait}_i \times \text{Weight}_i}{\sum \text{Weights}}$$

## Extensibility

### Adding a New Position

1. Create `config/positions/xx.json` with position config
2. Define position-specific features and traits
3. Load via `engine.get_config("XX")`

### Adding a New Trait

1. Update position config to include new trait
2. Define component features and weights
3. Trait scorer automatically processes it

### Adding a New Feature

1. Create feature extraction logic in pipeline
2. Add to position config feature definitions
3. Reference in trait definitions

### Adding Level/Program Overrides

1. Create `config/levels/{level}.json` or `config/programs/{program}.json`
2. Override specific settings (thresholds, weights)
3. Pass `level` or `program` parameter to engine

## Output Schema

### Rep Level
```
{
  "position": str,
  "player_id": str,
  "time": float,
  "features": { name -> score },
  "traits": { name -> score }
}
```

### Play Level
```
{
  "position": str,
  "player_id": str,
  "play_id": str,
  "rep_count": int,
  "trait_scores": { name -> score },
  "composite_score": float(0-100)
}
```

### Game Level
```
{
  "position": str,
  "player_id": str,
  "game_id": str,
  "snaps": int,
  "trait_averages": { name -> score },
  "grade": float(0-100)
}
```

### Season Level
```
{
  "position": str,
  "player_id": str,
  "season": int,
  "games": int,
  "trait_averages": { name -> score },
  "final_grade": float(0-100)
}
```

## API Reference

### TrenchEngine

- `process_player_rep()` - Process single rep
- `process_player_play()` - Aggregate reps to play
- `process_player_game()` - Aggregate plays to game
- `process_player_season()` - Aggregate games to season
- `get_player_summary()` - Generate player report
- `compare_players()` - Compare multiple players
- `get_config()` - Load position config
- `list_positions()` - List available positions
- `validate_position()` - Check if position exists

### ConfigLoader

- `load_config()` - Load merged config for position
- `load_file()` - Load single config file
- `get_traits()` - Get traits for position
- `get_features()` - Get features for position
- `get_weights()` - Get weights for position
- `get_bucketing()` - Get bucketing configuration

### TraitScorer

- `score_trait()` - Score single trait from features
- `bucket_score()` - Classify score into bucket
- `calculate_grade()` - Convert traits to overall grade
- `score_categorical()` - Score categorical attribute

### Aggregator

- `aggregate_reps_to_play()` - Rep → play aggregation
- `aggregate_plays_to_game()` - Play → game aggregation
- `aggregate_games_to_season()` - Game → season aggregation
- `aggregate_with_stats()` - Aggregation with min/max/mean

## Advanced Features

### Configuration Merging

Deep merge allows flexible overrides:
```python
# Global default
{
  "weights": { "passing": 0.5, "rushing": 0.5 }
}

# College-level override
{
  "weights": { "passing": 0.6 }  # Overrides to 0.6
}

# Result after merge
{
  "weights": { "passing": 0.6, "rushing": 0.5 }
}
```

### Statistical Aggregation

Multiple aggregation methods supported:
- **Mean** - Average score
- **Median** - Middle value
- **Max** - Highest performance
- **Min** - Lowest performance
- **Weighted** - Custom weights

## Performance Considerations

- Config files cached after first load
- Minimal dependencies (just standard library for base implementation)
- Designed for batch processing of game film

## License

Bridge Athletics 2024

## Support

For issues, questions, or contributions, contact the Bridge Athletics development team.
