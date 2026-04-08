from google.cloud import firestore
import sys
import os
from datetime import datetime

# Initialize Firestore using application default credentials
# This matches the pattern in Ingestion/firestore_utils.py
db = firestore.Client()

def cleanup_stuck_job(game_id):
    """
    Mark a stuck processing job as failed and skip pose processing for that game.

    Args:
        game_id: The game document ID in Firestore

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Reference to the game in Firestore
        game_ref = db.collection('games').document(game_id)

        # Check if game exists
        game_doc = game_ref.get()
        if not game_doc.exists:
            print(f"Error: Game '{game_id}' not found in Firestore")
            return False

        game_data = game_doc.to_dict()
        print(f"Found game: {game_id}")
        print(f"Current status: {game_data.get('status', 'unknown')}")

        # Update the game status to mark pose processing as skipped
        update_data = {
            "status": "pose_skipped",
            "pose_status": "skipped",
            "pose_error": "Manually skipped due to stuck processing",
            "updated_at": datetime.utcnow()
        }

        game_ref.update(update_data)
        print(f"Successfully updated game '{game_id}':")
        print(f"  - status: pose_skipped")
        print(f"  - pose_status: skipped")

        return True

    except Exception as e:
        print(f"Error updating game '{game_id}': {e}")
        return False

def list_processing_games():
    """
    List all games currently in processing status.
    Useful for identifying stuck jobs.
    """
    try:
        print("\nGames in 'processing' status:")
        print("-" * 50)

        games_ref = db.collection("games")
        query = games_ref.where("status", "==", "processing")
        docs = query.stream()

        count = 0
        for doc in docs:
            count += 1
            data = doc.to_dict()
            print(f"ID: {doc.id}")
            print(f"  Status: {data.get('status')}")
            print(f"  Updated: {data.get('updated_at', 'N/A')}")
            print()

        if count == 0:
            print("No games found in 'processing' status")
        else:
            print(f"Total: {count} game(s)")

        return True

    except Exception as e:
        print(f"Error listing games: {e}")
        return False

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage:")
        print("  List stuck jobs:    python cleanup_stuck_pose.py list")
        print("  Cleanup a job:      python cleanup_stuck_pose.py <game_id>")
        print("\nExample:")
        print("  python cleanup_stuck_pose.py abc123xyz")
        sys.exit(1)

    command = sys.argv[1]

    if command == 'list':
        success = list_processing_games()
    else:
        game_id = command
        print(f"Cleaning up stuck job for game: {game_id}\n")
        success = cleanup_stuck_job(game_id)

    sys.exit(0 if success else 1)