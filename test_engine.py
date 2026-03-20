import json
import requests
from pathlib import Path

FASTAPI_URL = "http://localhost:3001/process"  # Updated to use Node.js API wrapper

def load_pose(path: str):
    """Load a JSON pose file from test-data/."""
    file_path = Path("node-api/test-data") / path
    with open(file_path, "r") as f:
        return json.load(f)

def test_engine(pose_file, level="college", program="default", position="rb"):
    """Send pose data to Node.js API and print the result."""
    print(f"\n=== Testing {pose_file} (position: {position}) ===")

    pose_json = load_pose(pose_file)
    
    # Extract first frame's keypoints (adjust if needed based on expected format)
    if "frames" in pose_json and len(pose_json["frames"]) > 0:
        pose_data = pose_json["frames"][0]["keypoints"]
    else:
        pose_data = pose_json

    payload = {
        "pose_data": pose_data,
        "level": level,
        "program": program,
        "position": position
    }

    try:
        response = requests.post(FASTAPI_URL, json=payload)
        print("Status:", response.status_code)
        print("Response:")
        print(json.dumps(response.json(), indent=2))
    except Exception as e:
        print("Error communicating with API:", e)

if __name__ == "__main__":
    # Run tests on all three mock files
    test_engine("pose_simple.json", position="rb")
    test_engine("pose_rep.json", position="wr")
    test_engine("pose_invalid.json", position="ol")