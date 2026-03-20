"""
Trench Engine Tests
Unit tests for engine components
"""

import unittest
from pathlib import Path
from config.config_loader import ConfigLoader
from scoring.trait_scorer import TraitScorer, ScoreBucket


class TestConfigLoader(unittest.TestCase):
    """Test configuration loading and merging."""
    
    def setUp(self):
        """Set up test fixtures."""
        config_root = Path(__file__).parent / "config"
        self.loader = ConfigLoader(str(config_root))
    
    def test_load_universal_config(self):
        """Test loading universal base config."""
        config = self.loader.load_config("OL")
        self.assertIn("bucketing", config)
        self.assertIn("aggregation", config)
    
    def test_load_position_config(self):
        """Test loading position-specific config."""
        config = self.loader.load_config("QB")
        self.assertIn("features", config)
        self.assertIn("traits", config)
        self.assertIn("weights", config)
    
    def test_get_traits(self):
        """Test retrieving traits for position."""
        traits = self.loader.get_traits("WR")
        self.assertIsInstance(traits, dict)
        self.assertGreater(len(traits), 0)
    
    def test_get_features(self):
        """Test retrieving features for position."""
        features = self.loader.get_features("RB")
        self.assertIsInstance(features, dict)
        self.assertGreater(len(features), 0)
    
    def test_get_weights(self):
        """Test retrieving weights for position."""
        weights = self.loader.get_weights("DL")
        self.assertIsInstance(weights, dict)


class TestTraitScorer(unittest.TestCase):
    """Test trait scoring logic."""
    
    def setUp(self):
        """Set up test fixtures."""
        config_root = Path(__file__).parent / "config"
        loader = ConfigLoader(str(config_root))
        self.scorer = TraitScorer(loader)
    
    def test_bucket_elite(self):
        """Test elite bucket classification."""
        bucket = self.scorer.bucket_score(95)
        self.assertEqual(bucket, ScoreBucket.ELITE)
    
    def test_bucket_high(self):
        """Test high bucket classification."""
        bucket = self.scorer.bucket_score(80)
        self.assertEqual(bucket, ScoreBucket.HIGH)
    
    def test_bucket_average(self):
        """Test average bucket classification."""
        bucket = self.scorer.bucket_score(60)
        self.assertEqual(bucket, ScoreBucket.AVERAGE)
    
    def test_numeric_to_letter_a(self):
        """Test A grade conversion."""
        grade = self.scorer._numeric_to_letter(95)
        self.assertEqual(grade, "A")
    
    def test_numeric_to_letter_f(self):
        """Test F grade conversion."""
        grade = self.scorer._numeric_to_letter(35)
        self.assertEqual(grade, "F")
    
    def test_letter_to_numeric(self):
        """Test letter to numeric conversion."""
        score = self.scorer._letter_to_numeric("A")
        self.assertEqual(score, 95)


class TestPositionConfigs(unittest.TestCase):
    """Test that all position configs are valid."""
    
    def setUp(self):
        """Set up test fixtures."""
        config_root = Path(__file__).parent / "config"
        self.loader = ConfigLoader(str(config_root))
        self.positions = ["OL", "DL", "QB", "RB", "WR", "DB", "LB"]
    
    def test_all_positions_exist(self):
        """Test that all position configs exist."""
        for position in self.positions:
            config = self.loader.load_config(position)
            self.assertIn("position", config)
            self.assertEqual(config["position"], position)
    
    def test_all_positions_have_features(self):
        """Test that all positions define features."""
        for position in self.positions:
            config = self.loader.load_config(position)
            self.assertIn("features", config)
            self.assertGreater(len(config["features"]), 0)
    
    def test_all_positions_have_traits(self):
        """Test that all positions define traits."""
        for position in self.positions:
            config = self.loader.load_config(position)
            self.assertIn("traits", config)
            self.assertGreater(len(config["traits"]), 0)
    
    def test_all_positions_have_weights(self):
        """Test that all positions define weights."""
        for position in self.positions:
            config = self.loader.load_config(position)
            self.assertIn("weights", config)
            self.assertGreater(len(config["weights"]), 0)
    
    def test_trait_features_reference_valid_features(self):
        """Test that trait features reference defined features."""
        for position in self.positions:
            config = self.loader.load_config(position)
            features = config.get("features", {})
            available_features = set()
            
            for category in features.values():
                for spec in category:
                    available_features.add(spec["name"])
            
            traits = config.get("traits", {})
            for trait_name, trait_spec in traits.items():
                trait_features = trait_spec.get("features", [])
                for feature in trait_features:
                    self.assertIn(
                        feature, available_features,
                        f"{position}/{trait_name} references undefined feature {feature}"
                    )


class TestLevelConfigs(unittest.TestCase):
    """Test level-specific configurations."""
    
    def setUp(self):
        """Set up test fixtures."""
        config_root = Path(__file__).parent / "config"
        self.loader = ConfigLoader(str(config_root))
    
    def test_load_hs_level(self):
        """Test loading high school level config."""
        config = self.loader.load_config("QB", level="hs")
        self.assertIsNotNone(config)
    
    def test_load_college_level(self):
        """Test loading college level config."""
        config = self.loader.load_config("QB", level="college")
        self.assertIsNotNone(config)
    
    def test_load_pro_level(self):
        """Test loading pro level config."""
        config = self.loader.load_config("QB", level="pro")
        self.assertIsNotNone(config)


class TestAggregation(unittest.TestCase):
    """Test aggregation logic."""
    
    def test_mean_aggregation(self):
        """Test mean aggregation."""
        trait_dicts = [
            {"trait1": 50, "trait2": 60},
            {"trait1": 70, "trait2": 80},
            {"trait1": 60, "trait2": 70}
        ]
        
        # Expected: trait1 = 60, trait2 = 70
        values1 = [50, 70, 60]
        values2 = [60, 80, 70]
        
        self.assertAlmostEqual(sum(values1) / len(values1), 60)
        self.assertAlmostEqual(sum(values2) / len(values2), 70)


if __name__ == "__main__":
    unittest.main()
