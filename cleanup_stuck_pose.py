import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

# Initialize Firestore
cred = credentials.Certificate('path/to/your/serviceAccountKey.json')
firebase_admin.initialize_app(cred)
db = firestore.client()

# Function to cleanup stuck processing job
def cleanup_stuck_job(job_id):
    # Reference to the job in Firestore
    job_ref = db.collection('jobs').document(job_id)
    
    # Delete the stuck job
    job_ref.delete()
    
    # Update pose_stage as skipped
    db.collection('stages').document('pose_stage').update({"status": "skipped"})
    
if __name__ == '__main__':
    # Replace 'your_job_id' with the actual job ID
    cleanup_stuck_job('your_job_id')