import sys
from pathlib import Path

# Add Analysis directory to path
analysis_path = Path(__file__).parent.parent / "Analysis" / "trench_engine"
config_path = analysis_path / "config"
sys.path.insert(0, str(analysis_path))

from engine import TrenchEngine

def run_engine(pose_data, level, program, position):
    """
    Run the Trench Engine with the provided parameters
    
    Args:
        pose_data (dict): Pose keypoints data
        level (str): Player level (e.g., 'freshman', 'sophomore')
        program (str): Program name (e.g., 'LSU')
        position (str): Position (e.g., 'OL', 'DL', 'QB')
    
    Returns:
        dict: Engine output with traits and scores
    """
    try:
        # Initialize engine with config_root pointing to the config directory
        engine = TrenchEngine(config_root=str(config_path))
        
        # Validate position exists
        if not engine.validate_position(position):
            return {
                "status": "failed",
                "position": position,
                "level": level,
                "program": program,
                "traits": None,
                "error": f"Position '{position}' not supported"
            }
        
        # Get config for this position/level/program
        config = engine.get_config(position, level, program)
        
        # Process single rep (stage 1-5 of pipeline)
        rep = engine.process_player_rep(
            position=position,
            player_id="temp_player",
            pose_data=pose_data,
            tracking_data={},
            timestamp=0.0,
            level=level,
            program=program
        )
        
        return {
            "status": "success",
            "position": position,
            "level": level,
            "program": program,
            "traits": rep.traits if hasattr(rep, 'traits') else {},
            "error": None
        }
    
    except Exception as e:
        return {
            "status": "failed",
            "position": position,
            "level": level,
            "program": program,
            "traits": None,
            "error": str(e)
        }