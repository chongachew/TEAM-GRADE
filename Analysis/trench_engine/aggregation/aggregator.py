"""
Aggregation Logic - Rep/Play/Game/Season level aggregation
Handles combining multiple levels into summary statistics
"""

from typing import Dict, List, Optional
from enum import Enum
from dataclasses import dataclass
from statistics import mean, median


class AggregationMethod(Enum):
    """Supported aggregation methods."""
    MEAN = "mean"
    MEDIAN = "median"
    MAX = "max"
    MIN = "min"
    WEIGHTED = "weighted"


@dataclass
class AggregationResult:
    """Result of aggregation operation."""
    level: str  # rep, play, game, season
    count: int  # Number of items aggregated
    averages: Dict[str, float]
    min_values: Dict[str, float]
    max_values: Dict[str, float]


class Aggregator:
    """Handle aggregation across rep/play/game/season levels."""
    
    def __init__(self, config_loader):
        """Initialize aggregator with config loader."""
        self.config_loader = config_loader
    
    # Rep -> Play level
    def aggregate_reps_to_play(
        self,
        rep_traits: List[Dict[str, float]],
        method: Optional[str] = None
    ) -> Dict[str, float]:
        """
        Aggregate multiple reps into play-level traits.
        
        Args:
            rep_traits: List of rep-level trait dicts
            method: Aggregation method (mean, median, etc.)
            
        Returns:
            Play-level trait averages
        """
        method = method or self.config_loader.get_aggregation_method("rep_to_play")
        return self._aggregate_list(rep_traits, method)
    
    # Play -> Game level
    def aggregate_plays_to_game(
        self,
        play_traits: List[Dict[str, float]],
        method: Optional[str] = None
    ) -> Dict[str, float]:
        """Aggregate plays into game-level traits."""
        method = method or self.config_loader.get_aggregation_method("play_to_game")
        return self._aggregate_list(play_traits, method)
    
    # Game -> Season level
    def aggregate_games_to_season(
        self,
        game_traits: List[Dict[str, float]],
        method: Optional[str] = None
    ) -> Dict[str, float]:
        """Aggregate games into season-level traits."""
        method = method or self.config_loader.get_aggregation_method("game_to_season")
        return self._aggregate_list(game_traits, method)
    
    def _aggregate_list(
        self,
        trait_dicts: List[Dict[str, float]],
        method: str
    ) -> Dict[str, float]:
        """
        Generic aggregation function for list of trait dicts.
        
        Args:
            trait_dicts: List of {trait -> score} dicts
            method: Aggregation method to use
            
        Returns:
            Aggregated {trait -> score} dict
        """
        if not trait_dicts:
            return {}
        
        # Collect all trait names
        all_traits = set()
        for d in trait_dicts:
            all_traits.update(d.keys())
        
        aggregated = {}
        
        # Aggregate each trait
        for trait in all_traits:
            values = [d.get(trait, 0) for d in trait_dicts]
            
            if method == "mean":
                aggregated[trait] = mean(values)
            elif method == "median":
                aggregated[trait] = median(values)
            elif method == "max":
                aggregated[trait] = max(values)
            elif method == "min":
                aggregated[trait] = min(values)
            else:
                # Default to mean
                aggregated[trait] = mean(values)
        
        return aggregated
    
    def aggregate_with_stats(
        self,
        trait_dicts: List[Dict[str, float]],
        method: str = "mean",
        level: str = "play"
    ) -> AggregationResult:
        """
        Aggregate with additional statistics.
        
        Args:
            trait_dicts: List of trait dicts to aggregate
            method: Aggregation method
            level: Current level (rep, play, game, season)
            
        Returns:
            AggregationResult with average, min, max
        """
        if not trait_dicts:
            return AggregationResult(
                level=level,
                count=0,
                averages={},
                min_values={},
                max_values={}
            )
        
        all_traits = set()
        for d in trait_dicts:
            all_traits.update(d.keys())
        
        averages = {}
        min_values = {}
        max_values = {}
        
        for trait in all_traits:
            values = [d.get(trait, 0) for d in trait_dicts]
            
            if method == "mean":
                averages[trait] = mean(values)
            else:
                averages[trait] = median(values)
            
            min_values[trait] = min(values)
            max_values[trait] = max(values)
        
        return AggregationResult(
            level=level,
            count=len(trait_dicts),
            averages=averages,
            min_values=min_values,
            max_values=max_values
        )
    
    def compare_across_levels(
        self,
        rep_level: Dict[str, float],
        play_level: Dict[str, float],
        game_level: Dict[str, float]
    ) -> Dict[str, Dict[str, float]]:
        """
        Compare trait consistency across aggregation levels.
        
        Returns:
            Dict showing traits at each level
        """
        return {
            "rep": rep_level,
            "play": play_level,
            "game": game_level
        }
