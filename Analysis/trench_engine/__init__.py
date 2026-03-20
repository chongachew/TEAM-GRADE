"""
Trench Engine Package
Config-driven, modular football analytics engine
"""

from engine import TrenchEngine
from config.config_loader import ConfigLoader
from pipeline.pipeline import TrenchPipeline, Rep, Play, Game, Season
from scoring.trait_scorer import TraitScorer
from aggregation.aggregator import Aggregator

__version__ = "1.0.0"
__author__ = "Bridge Athletics"
__all__ = [
    "TrenchEngine",
    "ConfigLoader",
    "TrenchPipeline",
    "TraitScorer",
    "Aggregator",
    "Rep",
    "Play",
    "Game",
    "Season"
]
