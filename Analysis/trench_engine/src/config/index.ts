/**
 * Trench Engine Config Module
 * Exports configuration loading and type utilities
 */

export * from './types';
export {
  ConfigLoader,
  createConfigLoader,
  type PositionConfig,
  type FeatureSpec,
  type TraitSpec,
  type BucketingConfig,
  type AggregationConfig,
  type UniversalConfig,
  type LevelConfig,
  type ProgramConfig,
} from './configLoader';
export {
  deepMerge,
  deepMergeMultiple,
  deepMergeWithArrayAccumulation,
  isPlainObject,
  type DeepMergeOptions,
} from './deepMerge';
