/**
 * Deep Merge Utility
 * Merges two objects recursively with override taking precedence
 */

export type DeepMergeOptions = {
  /** Whether to merge arrays or replace them. Default: false (replace) */
  mergeArrays?: boolean;
  /** Custom merger function for specific types */
  customMerger?: (base: any, override: any, key: string) => any;
};

/**
 * Deep merge two objects recursively.
 * Override values take precedence over base values.
 *
 * @param base - Base object
 * @param override - Override object
 * @param options - Merge options
 * @returns Merged object
 *
 * @example
 * ```ts
 * const base = { a: 1, b: { c: 2, d: 3 } };
 * const override = { b: { c: 20 } };
 * const result = deepMerge(base, override);
 * // result: { a: 1, b: { c: 20, d: 3 } }
 * ```
 */
export function deepMerge<T extends Record<string, any>>(
  base: T,
  override: Partial<T>,
  options: DeepMergeOptions = {}
): T {
  const { mergeArrays = false, customMerger } = options;

  // Create a shallow copy to avoid mutating the original
  const result = { ...base };

  for (const key in override) {
    if (!override.hasOwnProperty(key)) {
      continue;
    }

    const baseValue = result[key];
    const overrideValue = override[key];

    // Use custom merger if provided
    if (customMerger) {
      const merged = customMerger(baseValue, overrideValue, key);
      if (merged !== undefined) {
        result[key] = merged;
        continue;
      }
    }

    // Handle null/undefined - override takes precedence
    if (overrideValue === null || overrideValue === undefined) {
      result[key] = overrideValue;
      continue;
    }

    // Handle arrays
    if (Array.isArray(baseValue) && Array.isArray(overrideValue)) {
      result[key] = mergeArrays
        ? [...baseValue, ...overrideValue]
        : overrideValue;
      continue;
    }

    // Handle objects - recursive merge
    if (
      isPlainObject(baseValue) &&
      isPlainObject(overrideValue)
    ) {
      result[key] = deepMerge(baseValue, overrideValue, options);
      continue;
    }

    // Handle primitive types and arrays where base is not array
    // Override takes precedence
    result[key] = overrideValue;
  }

  return result;
}

/**
 * Check if a value is a plain object (not array, null, etc.)
 *
 * @param value - Value to check
 * @returns True if value is a plain object
 */
export function isPlainObject(value: any): boolean {
  if (!value || typeof value !== 'object') {
    return false;
  }

  if (Array.isArray(value)) {
    return false;
  }

  if (value instanceof Date || value instanceof RegExp) {
    return false;
  }

  // Check if object is plain
  const prototype = Object.getPrototypeOf(value);
  return prototype === null || prototype === Object.prototype;
}

/**
 * Deep merge multiple objects in sequence.
 * Each subsequent object overrides previous ones.
 *
 * @param objects - Objects to merge
 * @param options - Merge options
 * @returns Merged result
 *
 * @example
 * ```ts
 * const result = deepMergeMultiple(base, level, program, position);
 * ```
 */
export function deepMergeMultiple<T extends Record<string, any>>(
  objects: (T | Partial<T>)[],
  options: DeepMergeOptions = {}
): T {
  if (objects.length === 0) {
    return {} as T;
  }

  return objects.reduce((acc, obj) => {
    return deepMerge(acc, obj, options);
  }, objects[0]) as T;
}

/**
 * Perform a deep merge with array accumulation.
 * Useful for merging feature/trait definitions that should accumulate.
 *
 * @param base - Base object
 * @param override - Override object
 * @returns Merged object with accumulated arrays
 */
export function deepMergeWithArrayAccumulation<T extends Record<string, any>>(
  base: T,
  override: Partial<T>
): T {
  return deepMerge(base, override, { mergeArrays: true });
}
