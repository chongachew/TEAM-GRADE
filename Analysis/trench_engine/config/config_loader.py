"""
Config Loader - Language-Agnostic Configuration Management
Implements deep merge with load order: base -> level -> program -> positions
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional, List


class ConfigLoader:
    """Load and merge configuration files following the Trench Engine spec."""
    
    def __init__(self, config_root: str):
        """Initialize config loader with root directory."""
        self.config_root = Path(config_root)
        self.cache: Dict[str, Any] = {}
    
    def load_config(
        self,
        position: str,
        level: str = "universal",
        program: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Load and merge configs in order: base -> level -> program -> position.
        
        Args:
            position: Position code (e.g., 'OL', 'QB', 'DB')
            level: Level code (e.g., 'hs', 'college', 'pro')
            program: Optional program/scheme name for overrides
            
        Returns:
            Merged configuration dictionary
        """
        cache_key = f"{position}_{level}_{program}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # Load in order: base -> level -> program -> position
        config = {}
        
        # 1. Load universal base config
        config = self._merge(config, self.load_file("base/universal.json"))
        
        # 2. Load level-specific overrides
        level_file = f"levels/{level}.json"
        if self._file_exists(level_file):
            config = self._merge(config, self.load_file(level_file))
        
        # 3. Load program-specific overrides
        if program:
            program_file = f"programs/{program}.json"
            if self._file_exists(program_file):
                config = self._merge(config, self.load_file(program_file))
        
        # 4. Load position-specific config
        position_file = f"positions/{position.lower()}.json"
        if self._file_exists(position_file):
            config = self._merge(config, self.load_file(position_file))
        
        self.cache[cache_key] = config
        return config
    
    def load_file(self, relative_path: str) -> Dict[str, Any]:
        """Load a single JSON config file."""
        file_path = self.config_root / relative_path
        if not file_path.exists():
            raise FileNotFoundError(f"Config file not found: {file_path}")
        
        with open(file_path, 'r') as f:
            return json.load(f)
    
    def _file_exists(self, relative_path: str) -> bool:
        """Check if a config file exists."""
        return (self.config_root / relative_path).exists()
    
    @staticmethod
    def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deep merge override into base dictionary.
        Override values take precedence.
        """
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigLoader._merge(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def get_traits(self, position: str, **kwargs) -> Dict[str, Any]:
        """Get traits config for a position."""
        config = self.load_config(position, **kwargs)
        return config.get("traits", {})
    
    def get_features(self, position: str, **kwargs) -> Dict[str, Any]:
        """Get features config for a position."""
        config = self.load_config(position, **kwargs)
        return config.get("features", {})
    
    def get_weights(self, position: str, **kwargs) -> Dict[str, float]:
        """Get weights config for a position."""
        config = self.load_config(position, **kwargs)
        return config.get("weights", {})
    
    def get_bucketing(self, **kwargs) -> Dict[str, Any]:
        """Get bucketing configuration."""
        config = self.load_config("universal", **kwargs)
        return config.get("bucketing", {})
    
    def get_aggregation_method(self, level: str, **kwargs) -> str:
        """Get aggregation method for a specific level."""
        config = self.load_config("universal", **kwargs)
        aggregation = config.get("aggregation", {})
        return aggregation.get(level, "mean")
