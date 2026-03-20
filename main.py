"""
Team Grade - Main Entry Point
Demonstrates the Trench Engine in action with sample data
"""

import sys
import json
from pathlib import Path

# Add trench_engine to path
trench_engine_path = Path(__file__).parent / "Analysis" / "trench_engine"
sys.path.insert(0, str(trench_engine_path))

from engine import TrenchEngine
from pipeline.pipeline import Rep, Play, Game, Season
from scoring.trait_scorer import TraitScorer
from aggregation.aggregator import Aggregator


def load_trait_dictionary():
    """Load the trait dictionary configuration."""
    trait_dict_path = Path(__file__).parent / "Analysis" / "trait_dictionary.json"
    with open(trait_dict_path, 'r') as f:
        return json.load(f)


def demo_basic_pipeline():
    """Demonstrate basic pipeline processing."""
    print("\n" + "="*70)
    print("TEAM GRADE - Basic Pipeline Demo")
    print("="*70)
    
    # Initialize engine
    config_root = Path(__file__).parent / "Analysis" / "trench_engine" / "config"
    engine = TrenchEngine(str(config_root))
    
    # Display engine info
    info = engine.get_engine_info()
    print(f"\n✓ Engine initialized: {info['name']} v{info['version']}")
    print(f"  Available positions: {', '.join(info['available_positions'])}")
    
    # Load trait dictionary for reference
    trait_dict = load_trait_dictionary()
    print(f"✓ Trait dictionary loaded with {len(trait_dict)} position groups")
    
    return engine, trait_dict


def demo_qb_analysis(engine):
    """Demonstrate QB analysis pipeline."""
    print("\n" + "="*70)
    print("QB ANALYSIS EXAMPLE")
    print("="*70)
    
    position = "QB"
    player_id = "QB_2024_001"
    
    # Load and display QB config
    qb_config = engine.get_config(position, level="college")
    print(f"\n✓ {position} Configuration Loaded")
    print(f"  Features: {list(qb_config.get('features', {}).keys())}")
    print(f"  Traits: {list(qb_config.get('traits', {}).keys())}")
    print(f"  Weights: {qb_config.get('weights', {})}")
    
    # Simulate processing multiple reps
    print(f"\n✓ Processing {position} reps...")
    reps = []
    
    for i in range(5):
        # Mock pose/tracking data
        pose_data = {
            "keypoints": [0.5 + i*0.05, 0.6, 0.7],
            "confidence": [0.95, 0.92, 0.88]
        }
        
        tracking_data = {
            "position": [10 + i, 5 + (i % 2)],
            "velocity": [2.1 + i*0.1, 0.5]
        }
        
        rep = engine.process_player_rep(
            position=position,
            player_id=player_id,
            pose_data=pose_data,
            tracking_data=tracking_data,
            timestamp=i * 0.2,
            level="college"
        )
        
        reps.append(rep)
        print(f"  Rep {i+1}: Features={len(rep.features)}, Traits={len(rep.traits)}")
    
    # Aggregate reps to play
    play = engine.process_player_play(
        position=position,
        player_id=player_id,
        play_id="PLAY_001",
        reps=reps
    )
    
    print(f"\n✓ Play Aggregation:")
    print(f"  Rep count: {play.rep_count}")
    print(f"  Composite score: {play.composite_score:.2f}")
    print(f"  Traits: {list(play.trait_scores.keys())}")
    
    # Simulate multiple plays into a game
    plays = [play for _ in range(10)]
    
    game = engine.process_player_game(
        position=position,
        player_id=player_id,
        game_id="GAME_001",
        plays=plays
    )
    
    print(f"\n✓ Game Aggregation:")
    print(f"  Snaps: {game.snaps}")
    print(f"  Grade: {game.grade:.2f}")
    
    # Simulate multiple games into a season
    games = [game for _ in range(12)]
    
    season = engine.process_player_season(
        position=position,
        player_id=player_id,
        season=2024,
        games=games
    )
    
    print(f"\n✓ Season Aggregation:")
    print(f"  Games: {season.games}")
    print(f"  Final Grade: {season.final_grade:.2f}")
    
    # Generate summary
    summary = engine.get_player_summary(position, player_id, season)
    
    print(f"\n✓ Player Summary:")
    print(f"  Position: {summary['position']}")
    print(f"  Season: {summary['season']}")
    print(f"  Overall Grade: {summary['overall_grade']['letter']} ({summary['overall_grade']['numeric']:.2f})")
    
    if 'traits' in summary:
        print(f"  Top Traits:")
        sorted_traits = sorted(
            summary['traits'].items(),
            key=lambda x: x[1]['score'],
            reverse=True
        )[:3]
        for trait_name, trait_data in sorted_traits:
            print(f"    - {trait_name}: {trait_data['score']:.2f} ({trait_data['bucket']})")


def demo_position_comparison(engine):
    """Demonstrate comparing multiple positions."""
    print("\n" + "="*70)
    print("POSITION COMPARISON EXAMPLE")
    print("="*70)
    
    positions = ["QB", "WR", "RB"]
    
    print(f"\n✓ Available positions: {', '.join(positions)}")
    
    for position in positions:
        config = engine.get_config(position, level="college")
        traits = config.get('traits', {})
        features = config.get('features', {})
        
        print(f"\n  {position}:")
        print(f"    - Traits: {len(traits)}")
        print(f"    - Feature categories: {len(features)}")
        print(f"    - Trait list: {', '.join(list(traits.keys())[:3])}...")


def demo_configuration_loading(engine):
    """Demonstrate configuration loading with different levels."""
    print("\n" + "="*70)
    print("CONFIGURATION LOADING EXAMPLE")
    print("="*70)
    
    position = "DB"
    
    print(f"\nLoading {position} configs at different levels...")
    
    levels = ["hs", "college", "pro"]
    
    for level in levels:
        try:
            config = engine.get_config(position, level=level)
            bucketing = config.get('bucketing', {})
            print(f"\n✓ {level.upper()} config loaded")
            if bucketing and bucketing.get('numeric'):
                elite_range = bucketing['numeric'].get('elite', {})
                print(f"  Elite threshold: {elite_range.get('min', 'N/A')}-{elite_range.get('max', 'N/A')}")
        except Exception as e:
            print(f"✗ {level.upper()}: {str(e)[:50]}")


def demo_engine_info(engine):
    """Display comprehensive engine information."""
    print("\n" + "="*70)
    print("ENGINE INFORMATION")
    print("="*70)
    
    info = engine.get_engine_info()
    
    print(f"\nEngine: {info['name']}")
    print(f"Version: {info['version']}")
    print(f"Description: {info['description']}")
    print(f"\nAvailable Positions:")
    for pos in sorted(info['available_positions']):
        print(f"  • {pos}")
    
    print(f"\nPipeline Stages ({len(info['stages'])}):")
    for i, stage in enumerate(info['stages'], 1):
        print(f"  {i}. {stage}")


def main():
    """Main entry point."""
    print("\n")
    print("╔" + "="*68 + "╗")
    print("║" + " "*15 + "TEAM GRADE - FOOTBALL ANALYTICS ENGINE" + " "*15 + "║")
    print("╚" + "="*68 + "╝")
    
    try:
        # Demo 1: Basic pipeline
        engine, trait_dict = demo_basic_pipeline()
        
        # Demo 2: Engine info
        demo_engine_info(engine)
        
        # Demo 3: Configuration loading with different levels
        demo_configuration_loading(engine)
        
        # Demo 4: Position comparison
        demo_position_comparison(engine)
        
        # Demo 5: Full QB analysis pipeline
        demo_qb_analysis(engine)
        
        print("\n" + "="*70)
        print("✓ ALL DEMOS COMPLETED SUCCESSFULLY")
        print("="*70)
        print("\nTeam Grade is working! 🎉")
        print("\nNext steps:")
        print("  1. Connect to Firebase for live game data")
        print("  2. Integrate video processing pipeline")
        print("  3. Deploy to production environment")
        print("="*70 + "\n")
        
        return 0
    
    except Exception as e:
        print(f"\n✗ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
