/**
 * Core Type Definitions for Trench Engine
 * Defines all fundamental types used throughout the system
 */

// ============================================
// CONFIG TYPES
// ============================================

/**
 * Feature source types - where the feature data comes from
 */
export type FeatureSource =
  | 'pose_estimation'
  | 'tracking_data'
  | 'biomechanics'
  | 'video_analysis';

/**
 * Normalization method for feature values
 */
export type NormalizationMethod =
  | 'percentile'
  | 'standard'
  | 'min-max'
  | 'z-score';

/**
 * Data type for feature values
 */
export type DataType = 'numeric' | 'categorical' | 'boolean';

/**
 * Score bucket classification
 */
export enum ScoreBucket {
  ELITE = 'elite',
  HIGH = 'high',
  AVERAGE = 'average',
  BELOW_AVERAGE = 'below_average',
  POOR = 'poor',
}

/**
 * Letter grades
 */
export enum LetterGrade {
  A = 'A',
  B = 'B',
  C = 'C',
  D = 'D',
  F = 'F',
}

/**
 * Competition levels
 */
export enum Level {
  HIGH_SCHOOL = 'hs',
  COLLEGE = 'college',
  PROFESSIONAL = 'pro',
}

/**
 * Aggregation methods for combining scores
 */
export enum AggregationMethod {
  MEAN = 'mean',
  MEDIAN = 'median',
  MAX = 'max',
  MIN = 'min',
  WEIGHTED = 'weighted',
}

// ============================================
// PIPELINE TYPES
// ============================================

/**
 * Raw pose estimation data - keypoints and confidence scores
 */
export interface PoseData {
  keypoints: number[];
  confidence: number[];
  timestamp?: number;
  format?: 'COCO' | 'MediaPipe' | 'custom';
}

/**
 * Normalized pose data relative to body frame
 */
export interface NormalizedPose {
  keypoints: number[];
  normalized: true;
  center: [number, number];
  scale: number;
}

/**
 * Tracking data - position and velocity
 */
export interface TrackingData {
  position: [number, number];
  velocity: [number, number];
  acceleration?: [number, number];
  timestamp?: number;
}

/**
 * Discrete movement event
 */
export interface MovementEvent {
  type: string;
  startTime: number;
  endTime: number;
  duration: number;
  magnitude?: number;
  direction?: number;
}

/**
 * Extracted feature value
 */
export interface Feature {
  name: string;
  value: number;
  normalized: boolean;
  rank?: number;
  percentile?: number;
}

/**
 * Scored trait value
 */
export interface Trait {
  name: string;
  score: number; // 0-100
  bucket: ScoreBucket;
  grade: LetterGrade;
  components?: Record<string, number>;
}

/**
 * Grade result with numeric and letter grades
 */
export interface Grade {
  numeric: number; // 0-100
  letter: LetterGrade;
  bucket: ScoreBucket;
}

// ============================================
// DATA LEVEL TYPES
// ============================================

/**
 * Single rep (repetition) data
 */
export interface Rep {
  position: string;
  playerId: string;
  time: number;
  features: Record<string, number>;
  traits: Record<string, number>;
  grade?: Grade;
}

/**
 * Play-level aggregation (multiple reps)
 */
export interface Play {
  position: string;
  playerId: string;
  playId: string;
  repCount: number;
  traitScores: Record<string, number>;
  compositeScore: number;
  grade: Grade;
}

/**
 * Game-level aggregation (multiple plays)
 */
export interface Game {
  position: string;
  playerId: string;
  gameId: string;
  snaps: number;
  traitAverages: Record<string, number>;
  traitMin: Record<string, number>;
  traitMax: Record<string, number>;
  grade: Grade;
}

/**
 * Season-level aggregation (multiple games)
 */
export interface Season {
  position: string;
  playerId: string;
  season: number;
  games: number;
  totalSnaps: number;
  traitAverages: Record<string, number>;
  traitMin: Record<string, number>;
  traitMax: Record<string, number>;
  grade: Grade;
}

// ============================================
// PLAYER SUMMARY TYPES
// ============================================

/**
 * Trait summary with score and bucket
 */
export interface TraitSummary {
  name: string;
  score: number;
  bucket: ScoreBucket;
  percentile: number;
}

/**
 * Single player summary
 */
export interface PlayerSummary {
  playerId: string;
  position: string;
  season: number;
  gamesPlayed: number;
  snapsPlayed: number;
  traits: TraitSummary[];
  overallGrade: Grade;
}

/**
 * Player comparison data
 */
export interface PlayerComparison {
  position: string;
  playerCount: number;
  players: Record<string, PlayerSummary>;
  ranking: {
    playerId: string;
    grade: Grade;
    traitScores: Record<string, number>;
  }[];
}

/**
 * Peer comparison percentiles
 */
export interface PeerComparison {
  position: string;
  playerCount: number;
  traits: Record<string, {
    playerValue: number;
    percentile: number;
    rank: number;
  }>;
}

// ============================================
// STATISTICS TYPES
// ============================================

/**
 * Statistical summary
 */
export interface Statistics {
  count: number;
  mean: number;
  median: number;
  min: number;
  max: number;
  stdDev: number;
  q1: number; // 25th percentile
  q3: number; // 75th percentile
}

/**
 * Distribution data for visualization
 */
export interface Distribution {
  bins: number[];
  binEdges: number[];
  frequencies: number[];
}

// ============================================
// UTILITY TYPES
// ============================================

/**
 * Generic result wrapper
 */
export interface Result<T> {
  success: boolean;
  data?: T;
  error?: Error | string;
  metadata?: Record<string, any>;
}

/**
 * Aggregation statistics result
 */
export interface AggregationStats {
  level: 'rep' | 'play' | 'game' | 'season';
  count: number;
  averages: Record<string, number>;
  minimums: Record<string, number>;
  maximums: Record<string, number>;
  standardDeviations: Record<string, number>;
}

/**
 * Configuration metadata
 */
export interface ConfigMetadata {
  version: string;
  engineName?: string;
  description?: string;
  level?: Level;
  program?: string;
  timestamp?: number;
}

/**
 * Engine initialization options
 */
export interface EngineOptions {
  configRoot: string;
  level?: Level;
  program?: string;
  cache?: boolean;
  verbose?: boolean;
}

/**
 * Processing options for pipeline
 */
export interface ProcessingOptions {
  level?: Level;
  program?: string;
  aggregationMethod?: AggregationMethod;
  includeStats?: boolean;
  includePercentiles?: boolean;
}

// ============================================
// VALIDATION TYPES
// ============================================

/**
 * Configuration validation error
 */
export interface ValidationError {
  path: string;
  message: string;
  code: string;
  severity: 'error' | 'warning';
}

/**
 * Validation result
 */
export interface ValidationResult {
  valid: boolean;
  errors: ValidationError[];
  warnings: ValidationError[];
}

// ============================================
// TYPE HELPERS
// ============================================

/**
 * Numeric value constraints
 */
export interface NumericConstraints {
  min?: number;
  max?: number;
  step?: number;
  precision?: number;
}

/**
 * Categorical value definition
 */
export interface CategoryValue {
  label: string;
  value: number;
  description?: string;
}

/**
 * Threshold definition for bucketing
 */
export interface ThresholdBucket {
  min: number;
  max: number;
}

/**
 * Feature extraction context
 */
export interface ExtractionContext {
  position: string;
  timestamp: number;
  pose: NormalizedPose;
  tracking: TrackingData[];
  events: MovementEvent[];
}

/**
 * Scoring context
 */
export interface ScoringContext {
  position: string;
  level: Level;
  program?: string;
  features: Record<string, number>;
}

/**
 * Aggregation context
 */
export interface AggregationContext {
  position: string;
  level: 'rep' | 'play' | 'game' | 'season';
  method: AggregationMethod;
}
