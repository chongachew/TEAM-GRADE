# Firestore Schema

## Collection: games/{gameId}

- youtubeUrl: string
- status: string
- rawVideoPath: string
- normalizedVideoPath: string
- errorMessage: string (optional)

## Status Lifecycle

pending → approved → downloading → downloaded → normalizing → ready-for-analysis → analysis-complete