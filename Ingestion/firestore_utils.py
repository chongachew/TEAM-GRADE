from google.cloud import firestore

db = firestore.Client()

def get_next_job():
    games_ref = db.collection("games")
    query = games_ref.where("status", "==", "approved").limit(1)
    docs = query.stream()
    for doc in docs:
        return doc.id, doc.to_dict()
    return None, None

def update_status(game_id, status, extra_fields=None):
    data = {"status": status}
    if extra_fields:
        data.update(extra_fields)
    db.collection("games").document(game_id).update(data)