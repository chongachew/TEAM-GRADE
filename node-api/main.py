import sys
import json
import argparse
from fastapi import FastAPI, Request
from pydantic import BaseModel
from trench_engine_runner import run_engine

app = FastAPI()

class PoseInput(BaseModel):
    pose_data: dict
    level: str
    program: str
    position: str

@app.post("/process")
async def process_pose(input: PoseInput):
    try:
        result = run_engine(
            pose_data=input.pose_data,
            level=input.level,
            program=input.program,
            position=input.position
        )
        return result
    except Exception as e:
        return {"error": str(e), "status": "failed"}

def run_cli():
    """Run as CLI with command-line arguments"""
    parser = argparse.ArgumentParser(description='Trench Engine CLI')
    parser.add_argument('--pose_data', type=str, required=True, help='JSON pose data')
    parser.add_argument('--level', type=str, required=True, help='Player level (e.g., freshman)')
    parser.add_argument('--program', type=str, required=True, help='Program name (e.g., LSU)')
    parser.add_argument('--position', type=str, required=True, help='Position (e.g., OL)')
    
    args = parser.parse_args()
    
    try:
        # Parse pose_data JSON
        pose_data = json.loads(args.pose_data)
        
        # Run engine
        result = run_engine(
            pose_data=pose_data,
            level=args.level,
            program=args.program,
            position=args.position
        )
        
        # Output as JSON to stdout
        print(json.dumps(result, indent=2))
        sys.exit(0)
        
    except json.JSONDecodeError as e:
        error_result = {"error": f"Invalid JSON in pose_data: {str(e)}", "status": "failed"}
        print(json.dumps(error_result))
        sys.exit(1)
    except Exception as e:
        error_result = {"error": str(e), "status": "failed"}
        print(json.dumps(error_result))
        sys.exit(1)

if __name__ == "__main__":
    # Check if running as CLI or server
    if len(sys.argv) > 1 and sys.argv[1] in ['--pose_data', '--level', '--program', '--position']:
        run_cli()
    else:
        # Run as FastAPI server
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)