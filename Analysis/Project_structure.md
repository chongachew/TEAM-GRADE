/src
  /config
    configLoader.ts
    deepMerge.ts
    types.ts

  /positions
    OL/
      index.ts
      featureExtractor.ts
      traitScorer.ts
      weights.ts
    DL/
      index.ts
      featureExtractor.ts
      traitScorer.ts
      weights.ts
    QB/
      index.ts
      featureExtractor.ts
      traitScorer.ts
      weights.ts
    RB/
      index.ts
      featureExtractor.ts
      traitScorer.ts
      weights.ts
    WR/
      index.ts
      featureExtractor.ts
      traitScorer.ts
      weights.ts
    DB/
      index.ts
      featureExtractor.ts
      traitScorer.ts
      weights.ts
    LB/
      index.ts
      featureExtractor.ts
      traitScorer.ts
      weights.ts

  /pipeline
    PoseNormalizer.ts
    EventSegmenter.ts
    FeatureExtractor.ts
    TraitScorer.ts
    Aggregator.ts
    Engine.ts

  /types
    PoseTypes.ts
    EventTypes.ts
    FeatureTypes.ts
    TraitTypes.ts
    OutputTypes.ts

  /utils
    math.ts
    smoothing.ts
    angle.ts
    velocity.ts
    timing.ts

/config
  base.json
  /levels
    hs.json
    college.json
    pro.json
  /programs
    boise.json
    elf.json
  /positions
    ol.json
    dl.json
    qb.json
    rb.json
    wr.json
    db.json
    lb.json

package.json
tsconfig.json
README.md


• 	Loads base, level, program, and position configs
• 	Deep merges them
• 	Exposes a typed  object


• 	Recursive deep merge
• 	Arrays overridden, objects merged


• 	
• 	
• 	
• 	
• 	


• 	Height normalization
• 	Field coordinate normalization
• 	Orientation alignment


• 	Detect snap
• 	Detect first step
• 	Detect engagement
• 	Detect strike
• 	Detect shed
• 	Detect throw/catch/tackle


• 	Universal features (speed, accel, angles)
• 	Calls position‑specific extractors


• 	Reads config
• 	Classifies buckets
• 	Computes weighted trait scores


• 	Rep → play → game → season aggregation


• 	Orchestrates entire pipeline
• 	Accepts raw pose + metadata
• 	Returns standardized output


• 	Pose types
• 	Event types
• 	Feature types
• 	Trait types
• 	Output schema


• 	Math helpers
• 	Angle calculations
• 	Velocity/acceleration
• 	Smoothing filters
• 	Timing utilities


• 	Exports feature extractor + trait scorer + weights


• 	Position‑specific feature extraction
• 	Uses config thresholds


• 	Position‑specific trait scoring
• 	Uses config trait definitions


• 	Position‑specific composite grade logic
