"""
Example usage of the Trench Engine
Demonstrates basic pipeline workflow
"""

from pathlib import Path
from engine import TrenchEngine


def example_basic_workflow():
    """Basic workflow example."""
    
    # Initialize engine with config directory
    config_root = Path(__file__).parent / "config"
    engine = TrenchEngine(str(config_root))
    
    # Display engine info
    info = engine.get_engine_info()
    print(f"Engine: {info['name']} v{info['version']}")
    print(f"Available positions: {', '.join(info['available_positions'])}")
    print(f"Pipeline stages: {len(info['stages'])}")
    
    # Example: Load QB config
    qb_config = engine.get_config("QB", level="college")
    print(f"\nQB Features: {list(qb_config.get('features', {}).keys())}")
    print(f"QB Traits: {list(qb_config.get('traits', {}).keys())}")
    
    # Example: Process a single rep
    pose_data = {
        "keypoints": [0.5, 0.6, 0.7],  # Mock pose data
        "confidence": [0.95, 0.92, 0.88]
    }
    
    tracking_data = {
        "position": [10.5, 5.2],  # Mock position
        "velocity": [2.1, 0.5]
    }
    
    rep = engine.process_player_rep(
        position="QB",
        player_id="QB001",
        pose_data=pose_data,
        tracking_data=tracking_data,
        timestamp=0.5,
        level="college"
    )
    
    print(f"\nProcessed Rep:")
    print(f"  Position: {rep.position}")
    print(f"  Player: {rep.player_id}")
    print(f"  Features: {len(rep.features)}")
    print(f"  Traits: {len(rep.traits)}")


def example_aggregation_workflow():
    """Aggregation workflow example."""
    
    config_root = Path(__file__).parent / "config"
    engine = TrenchEngine(str(config_root))
    
    # Simulate multiple reps
    reps_mock = [
        engine.process_player_rep(
            position="WR",
            player_id="WR001",
            pose_data={"keypoints": [0.5] * 3},
            tracking_data={"position": [10 + i, 5]},
            timestamp=i * 0.2
        )
        for i in range(5)
    ]
    
    # Aggregate reps to play
    play = engine.process_player_play(
        position="WR",
        player_id="WR001",
        play_id="PLAY_001",
        reps=reps_mock
    )
    
    print(f"Play Summary:")
    print(f"  Rep count: {play.rep_count}")
    print(f"  Composite score: {play.composite_score:.2f}")
    
    # Simulate multiple plays
    plays_mock = [play for _ in range(3)]
    
    # Aggregate plays to game
    game = engine.process_player_game(
        position="WR",
        player_id="WR001",
        game_id="GAME_001",
        plays=plays_mock
    )
    
    print(f"\nGame Summary:")
    print(f"  Snaps: {game.snaps}")
    print(f"  Grade: {game.grade:.2f}")


if __name__ == "__main__":
    print("=" * 60)
    print("TRENCH ENGINE - EXAMPLE USAGE")
    print("=" * 60)
    
    print("\n[1] Basic Workflow")
    print("-" * 60)
    try:
        example_basic_workflow()
    except Exception as e:
        print(f"Error in basic workflow: {e}")
    
    print("\n[2] Aggregation Workflow")
    print("-" * 60)
    try:
        example_aggregation_workflow()
    except Exception as e:
        print(f"Error in aggregation workflow: {e}")
    
    print("\n" + "=" * 60)
    print("Examples complete")
    print("=" * 60)
