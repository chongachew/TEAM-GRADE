"""
Trench Engine - Main orchestrator
Coordinates config loading, pipeline execution, and result generation
"""

from pathlib import Path
from typing import Dict, List, Optional, Any

from config.config_loader import ConfigLoader
from pipeline.pipeline import TrenchPipeline, Rep, Play, Game, Season
from scoring.trait_scorer import TraitScorer
from aggregation.aggregator import Aggregator


class TrenchEngine:
    """
    Main Trench Engine - coordinates all subsystems.
    Config-driven, position-agnostic football analytics.
    """
    
    def __init__(self, config_root: str):
        """
        Initialize the Trench Engine.
        
        Args:
            config_root: Path to config directory
        """
        self.config_root = Path(config_root)
        self.config_loader = ConfigLoader(self.config_root)
        self.pipeline = TrenchPipeline(self.config_loader)
        self.scorer = TraitScorer(self.config_loader)
        self.aggregator = Aggregator(self.config_loader)
    
    def process_player_rep(
        self,
        position: str,
        player_id: str,
        pose_data: Dict[str, Any],
        tracking_data: Dict[str, Any],
        timestamp: float,
        level: str = "college",
        program: Optional[str] = None
    ) -> Rep:
        """
        Process a single rep for a player.
        
        Args:
            position: Position code (OL, QB, RB, WR, DB, DL, LB)
            player_id: Player identifier
            pose_data: Pose estimation keypoints
            tracking_data: Tracking position data
            timestamp: Time in seconds
            level: Competition level (hs, college, pro)
            program: Optional program/scheme override
            
        Returns:
            Rep object with features and trait scores
        """
        return self.pipeline.process_rep(
            position, player_id, pose_data, tracking_data, timestamp
        )
    
    def process_player_play(
        self,
        position: str,
        player_id: str,
        play_id: str,
        reps: List[Rep]
    ) -> Play:
        """Aggregate reps into play result."""
        return self.pipeline.process_play(position, player_id, play_id, reps)
    
    def process_player_game(
        self,
        position: str,
        player_id: str,
        game_id: str,
        plays: List[Play]
    ) -> Game:
        """Aggregate plays into game result."""
        return self.pipeline.process_game(position, player_id, game_id, plays)
    
    def process_player_season(
        self,
        position: str,
        player_id: str,
        season: int,
        games: List[Game]
    ) -> Season:
        """Aggregate games into season result."""
        return self.pipeline.process_season(position, player_id, season, games)
    
    def get_player_summary(
        self,
        position: str,
        player_id: str,
        season_data: Season
    ) -> Dict[str, Any]:
        """
        Generate comprehensive player summary.
        
        Returns:
            Dictionary with position, traits, grade, stats
        """
        grade, letter = self.scorer.calculate_grade(
            position, season_data.trait_averages
        )
        
        return {
            "player_id": player_id,
            "position": position,
            "season": season_data.season,
            "games_played": season_data.games,
            "traits": {
                name: {
                    "score": score,
                    "bucket": self.scorer.bucket_score(score).value
                }
                for name, score in season_data.trait_averages.items()
            },
            "overall_grade": {
                "numeric": grade,
                "letter": letter
            }
        }
    
    def compare_players(
        self,
        position: str,
        players: Dict[str, Season]
    ) -> Dict[str, Any]:
        """
        Compare multiple players at a position.
        
        Args:
            position: Position code
            players: Dict of player_id -> Season data
            
        Returns:
            Comparative analysis of players
        """
        summaries = {}
        for player_id, season_data in players.items():
            summaries[player_id] = self.get_player_summary(
                position, player_id, season_data
            )
        
        return {
            "position": position,
            "player_count": len(players),
            "players": summaries
        }
    
    def get_config(
        self,
        position: str,
        level: str = "college",
        program: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get loaded configuration for a position."""
        return self.config_loader.load_config(position, level, program)
    
    def list_positions(self) -> List[str]:
        """List all available positions."""
        positions_dir = self.config_root / "positions"
        if not positions_dir.exists():
            return []
        
        return [
            f.stem.upper()
            for f in positions_dir.glob("*.json")
        ]
    
    def validate_position(self, position: str) -> bool:
        """Check if position configuration exists."""
        config_file = self.config_root / "positions" / f"{position.lower()}.json"
        return config_file.exists()
    
    def get_engine_info(self) -> Dict[str, Any]:
        """Get engine metadata and version info."""
        return {
            "name": "Trench Engine",
            "version": "1.0.0",
            "description": "Config-driven, modular football analytics engine",
            "available_positions": self.list_positions(),
            "stages": [
                "Pose Estimation",
                "Pose Normalization",
                "Event Segmentation",
                "Feature Extraction",
                "Trait Scoring",
                "Aggregation",
                "Output Formatting"
            ]
        }
