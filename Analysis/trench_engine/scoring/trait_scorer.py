"""
Trait Scoring Logic - Core scoring and bucketing system
Converts features to trait scores and applies bucketing thresholds
"""

from typing import Dict, Any, Tuple
from enum import Enum


class ScoreBucket(Enum):
    """Score bucketing classification."""
    ELITE = "elite"
    HIGH = "high"
    AVERAGE = "average"
    BELOW_AVERAGE = "below_average"
    POOR = "poor"


class TraitScorer:
    """Handles trait scoring, bucketing, and grade conversion."""
    
    def __init__(self, config_loader):
        """Initialize scorer with config loader."""
        self.config_loader = config_loader
    
    def score_trait(
        self,
        position: str,
        trait_name: str,
        featured_values: Dict[str, float]
    ) -> float:
        """
        Score a trait from its component features.
        
        Args:
            position: Player position
            trait_name: Name of trait to score
            featured_values: Dict of feature_name -> score
            
        Returns:
            Trait score (0-100)
        """
        config = self.config_loader.load_config(position)
        traits = config.get("traits", {})
        
        if trait_name not in traits:
            return 50.0  # Default neutral score
        
        trait_spec = traits[trait_name]
        features = trait_spec.get("features", [])
        weights = trait_spec.get("weights", [])
        
        # Weighted sum of features
        if not features:
            return 50.0
        
        total = 0.0
        weight_sum = 0.0
        
        for feature, weight in zip(features, weights):
            if feature in featured_values:
                total += featured_values[feature] * weight
                weight_sum += weight
        
        return (total / weight_sum) if weight_sum > 0 else 50.0
    
    def bucket_score(self, score: float, position: str = None) -> ScoreBucket:
        """
        Classify score into bucket.
        
        Args:
            score: Numeric score (0-100)
            position: Optional position for position-specific bucketing
            
        Returns:
            ScoreBucket classification
        """
        bucketing = self.config_loader.get_bucketing()
        numeric_buckets = bucketing.get("numeric", {})
        
        for bucket_name, bounds in numeric_buckets.items():
            if bounds["min"] <= score <= bounds["max"]:
                return ScoreBucket(bucket_name)
        
        # Default bucket
        return ScoreBucket.AVERAGE
    
    def calculate_grade(
        self,
        position: str,
        traits: Dict[str, float]
    ) -> Tuple[float, str]:
        """
        Calculate overall grade from traits.
        
        Args:
            position: Player position
            traits: Dict of trait -> score
            
        Returns:
            Tuple of (numeric_grade: 0-100, letter_grade: A-F)
        """
        if not traits:
            return 50.0, "C"
        
        weights = self.config_loader.get_weights(position)
        average = sum(traits.values()) / len(traits)
        
        # Convert to letter grade
        letter = self._numeric_to_letter(average)
        
        return average, letter
    
    @staticmethod
    def _numeric_to_letter(score: float) -> str:
        """Convert 0-100 score to letter grade."""
        if score >= 90:
            return "A"
        elif score >= 80:
            return "B"
        elif score >= 70:
            return "C"
        elif score >= 60:
            return "D"
        else:
            return "F"
    
    @staticmethod
    def _letter_to_numeric(grade: str) -> float:
        """Convert letter grade to numeric score."""
        mapping = {
            "A": 95,
            "B": 85,
            "C": 75,
            "D": 65,
            "F": 35
        }
        return mapping.get(grade, 50.0)
    
    def score_categorical(
        self,
        category: str,
        position: str = None
    ) -> float:
        """
        Score a categorical attribute.
        
        Args:
            category: Category name (e.g., 'excellent', 'good')
            position: Optional position for position-specific scoring
            
        Returns:
            Numeric score for category
        """
        bucketing = self.config_loader.get_bucketing()
        categorical = bucketing.get("categorical", {})
        
        return categorical.get(category, 50.0)
    
    def percentile_to_score(self, percentile: float) -> float:
        """Convert percentile (0-100) to zero-based score."""
        return percentile
    
    def rank_to_score(self, rank: int, total_population: int) -> float:
        """Convert rank to percentile-based score."""
        if total_population == 0:
            return 50.0
        percentile = ((total_population - rank) / total_population) * 100
        return percentile
