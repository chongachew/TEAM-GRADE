const API_BASE = process.env.REACT_APP_API_BASE || "http://localhost:8000";

async function handleResponse(res) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Request failed (${res.status})`);
  }
  return data;
}

export async function ingestVideo({ videoUrl, teamId, playerNumber, position }) {
  const res = await fetch(`${API_BASE}/api/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      video_url: videoUrl,
      ...(teamId ? { team_id: teamId } : {}),
      ...(playerNumber ? { player_number: Number(playerNumber) } : {}),
      ...(position ? { position } : {}),
    }),
  });
  return handleResponse(res);
}

export async function getStatus(videoId) {
  const res = await fetch(`${API_BASE}/api/ingest/${videoId}/status`);
  return handleResponse(res);
}

export async function getAnalysis(videoId) {
  const res = await fetch(`${API_BASE}/api/analysis/${videoId}`);
  return handleResponse(res);
}
