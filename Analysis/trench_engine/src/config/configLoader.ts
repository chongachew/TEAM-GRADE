/**
 * Config Loader - Language-Agnostic Configuration Management
 * Implements deep merge with load order: base -> level -> program -> positions
 * All thresholds, buckets, weights, and trait definitions live in JSON config files
 */

import * as fs from 'fs';
import * as path from 'path';
import { deepMerge, deepMergeMultiple } from './deepMerge';

/**
 * Configuration structure for a single position
 */
export interface PositionConfig {
  position: string;
  name: string;
  description?: string;
  features: Record<string, FeatureSpec[]>;
  traits: Record<string, TraitSpec>;
  weights: Record<string, number>;
}

/**
 * Feature specification
 */
export interface FeatureSpec {
  name: string;
  source: 'pose_estimation' | 'tracking_data' | 'biomechanics' | 'video_analysis';
  description?: string;
  type: 'numeric' | 'categorical';
  normalization?: 'percentile' | 'standard' | 'min-max';
  thresholds?: Record<string, number>;
}

/**
 * Trait specification - composed of features
 */
export interface TraitSpec {
  features: string[];
  weights: number[];
  description?: string;
  thresholds?: Record<string, number>;
}

/**
 * Bucketing configuration
 */
export interface BucketingConfig {
  numeric: Record<string, { min: number; max: number }>;
  categorical?: Record<string, number>;
}

/**
 * Aggregation configuration
 */
export interface AggregationConfig {
  rep_to_play: string;
  play_to_game: string;
  game_to_season: string;
}

/**
 * Universal (base) configuration
 */
export interface UniversalConfig {
  metadata?: Record<string, any>;
  bucketing: BucketingConfig;
  aggregation: AggregationConfig;
  output_schema?: Record<string, any>;
}

/**
 * Level-specific configuration (HS/College/Pro)
 */
export interface LevelConfig {
  metadata?: {
    level: string;
    thresholds?: string;
    description?: string;
  };
  bucketing?: BucketingConfig;
  adjustments?: Record<string, number>;
  aggregation?: Partial<AggregationConfig>;
}

/**
 * Program/scheme-specific configuration
 */
export interface ProgramConfig {
  metadata?: {
    program: string;
    description?: string;
  };
  traits?: Record<string, Partial<TraitSpec>>;
  weights?: Record<string, number>;
  adjustments?: Record<string, number>;
}

/**
 * Load order: base -> level -> program -> positions
 */
enum LoadOrder {
  BASE = 0,
  LEVEL = 1,
  PROGRAM = 2,
  POSITION = 3,
}

/**
 * ConfigLoader - Manages loading and merging configuration files
 */
export class ConfigLoader {
  private configRoot: string;
  private cache: Map<string, any> = new Map();
  private loadedFiles: Map<string, any> = new Map();

  constructor(configRoot: string) {
    this.configRoot = configRoot;
    this.validateConfigRoot();
  }

  /**
   * Validate that config root directory exists
   */
  private validateConfigRoot(): void {
    if (!fs.existsSync(this.configRoot)) {
      throw new Error(`Config root directory not found: ${this.configRoot}`);
    }
  }

  /**
   * Load and merge configuration for a position
   * Load order: base -> level -> program -> position
   *
   * @param position - Position code (e.g., 'OL', 'QB', 'DB')
   * @param level - Level code (e.g., 'hs', 'college', 'pro'). Default: 'college'
   * @param program - Optional program/scheme name for overrides
   * @returns Merged configuration object
   *
   * @example
   * ```ts
   * const config = loader.loadConfig('QB', 'college');
   * const config = loader.loadConfig('WR', 'pro', 'west-coast');
   * ```
   */
  public loadConfig(
    position: string,
    level: string = 'college',
    program?: string
  ): PositionConfig & UniversalConfig {
    const cacheKey = this.getCacheKey(position, level, program);

    // Return cached config if available
    if (this.cache.has(cacheKey)) {
      return this.cache.get(cacheKey);
    }

    // Load configs in strict order
    const configs: any[] = [];

    // 1. Load base/universal config
    const baseConfig = this.loadFile('base/universal.json');
    configs.push(baseConfig);

    // 2. Load level-specific overrides
    const levelFile = `levels/${level}.json`;
    if (this.fileExists(levelFile)) {
      const levelConfig = this.loadFile(levelFile);
      configs.push(levelConfig);
    }

    // 3. Load program-specific overrides
    if (program) {
      const programFile = `programs/${program}.json`;
      if (this.fileExists(programFile)) {
        const programConfig = this.loadFile(programFile);
        configs.push(programConfig);
      }
    }

    // 4. Load position-specific config
    const positionFile = `positions/${position.toLowerCase()}.json`;
    if (this.fileExists(positionFile)) {
      const positionConfig = this.loadFile(positionFile);
      configs.push(positionConfig);
    }

    // Merge all configs in order
    const mergedConfig = deepMergeMultiple(configs);

    // Cache the result
    this.cache.set(cacheKey, mergedConfig);

    return mergedConfig;
  }

  /**
   * Load a single configuration file
   *
   * @param relativePath - Path relative to config root
   * @returns Parsed JSON configuration
   * @throws Error if file doesn't exist or is invalid JSON
   */
  public loadFile(relativePath: string): any {
    const filePath = path.join(this.configRoot, relativePath);

    // Check cache first
    if (this.loadedFiles.has(filePath)) {
      return this.loadedFiles.get(filePath);
    }

    // Validate file exists
    if (!fs.existsSync(filePath)) {
      throw new Error(`Config file not found: ${filePath}`);
    }

    // Read and parse file
    try {
      const content = fs.readFileSync(filePath, 'utf-8');
      const parsed = JSON.parse(content);
      this.loadedFiles.set(filePath, parsed);
      return parsed;
    } catch (error) {
      throw new Error(
        `Failed to parse config file ${filePath}: ${error instanceof Error ? error.message : String(error)}`
      );
    }
  }

  /**
   * Check if a config file exists without throwing
   *
   * @param relativePath - Path relative to config root
   * @returns True if file exists
   */
  public fileExists(relativePath: string): boolean {
    const filePath = path.join(this.configRoot, relativePath);
    return fs.existsSync(filePath);
  }

  /**
   * Get feature definitions for a position
   *
   * @param position - Position code
   * @param level - Optional level override
   * @param program - Optional program override
   * @returns Record of feature names to feature specs
   */
  public getFeatures(
    position: string,
    level?: string,
    program?: string
  ): Record<string, FeatureSpec[]> {
    const config = this.loadConfig(position, level, program);
    return config.features || {};
  }

  /**
   * Get trait definitions for a position
   *
   * @param position - Position code
   * @param level - Optional level override
   * @param program - Optional program override
   * @returns Record of trait names to trait specs
   */
  public getTraits(
    position: string,
    level?: string,
    program?: string
  ): Record<string, TraitSpec> {
    const config = this.loadConfig(position, level, program);
    return config.traits || {};
  }

  /**
   * Get weight configuration for a position
   *
   * @param position - Position code
   * @param level - Optional level override
   * @param program - Optional program override
   * @returns Record of weight names to weight values
   */
  public getWeights(
    position: string,
    level?: string,
    program?: string
  ): Record<string, number> {
    const config = this.loadConfig(position, level, program);
    return config.weights || {};
  }

  /**
   * Get bucketing configuration
   *
   * @param level - Optional level override
   * @param program - Optional program override
   * @returns Bucketing configuration
   */
  public getBucketing(level?: string, program?: string): BucketingConfig {
    const config = this.loadConfig('universal', level, program);
    return config.bucketing || { numeric: {} };
  }

  /**
   * Get aggregation methods configuration
   *
   * @param level - Optional level override
   * @param program - Optional program override
   * @returns Aggregation configuration
   */
  public getAggregation(level?: string, program?: string): AggregationConfig {
    const config = this.loadConfig('universal', level, program);
    return config.aggregation || {
      rep_to_play: 'mean',
      play_to_game: 'mean',
      game_to_season: 'mean',
    };
  }

  /**
   * Get aggregation method for a specific level
   *
   * @param levelName - Level name (rep_to_play, play_to_game, game_to_season)
   * @param level - Optional level override
   * @param program - Optional program override
   * @returns Aggregation method string
   */
  public getAggregationMethod(
    levelName: 'rep_to_play' | 'play_to_game' | 'game_to_season',
    level?: string,
    program?: string
  ): string {
    const aggregation = this.getAggregation(level, program);
    return aggregation[levelName] || 'mean';
  }

  /**
   * List all available positions
   *
   * @returns Array of position codes
   */
  public listPositions(): string[] {
    const positionsDir = path.join(this.configRoot, 'positions');

    if (!fs.existsSync(positionsDir)) {
      return [];
    }

    return fs
      .readdirSync(positionsDir)
      .filter((file) => file.endsWith('.json'))
      .map((file) => file.replace('.json', '').toUpperCase());
  }

  /**
   * Check if a position configuration exists
   *
   * @param position - Position code
   * @returns True if position config exists
   */
  public hasPosition(position: string): boolean {
    return this.fileExists(`positions/${position.toLowerCase()}.json`);
  }

  /**
   * Clear the configuration cache
   * Useful for testing or when config files change
   */
  public clearCache(): void {
    this.cache.clear();
    this.loadedFiles.clear();
  }

  /**
   * Get cache statistics for debugging
   *
   * @returns Cache stats object
   */
  public getCacheStats(): { cachedConfigs: number; loadedFiles: number } {
    return {
      cachedConfigs: this.cache.size,
      loadedFiles: this.loadedFiles.size,
    };
  }

  /**
   * Generate cache key for a configuration
   *
   * @param position - Position code
   * @param level - Level code
   * @param program - Optional program name
   * @returns Cache key string
   */
  private getCacheKey(position: string, level: string, program?: string): string {
    return `${position}:${level}:${program || 'default'}`;
  }
}

/**
 * Factory function to create ConfigLoader with default paths
 *
 * @param baseDir - Base directory (typically project root)
 * @returns Configured ConfigLoader instance
 */
export function createConfigLoader(baseDir: string = process.cwd()): ConfigLoader {
  const configRoot = path.join(baseDir, 'src', 'config');
  return new ConfigLoader(configRoot);
}
