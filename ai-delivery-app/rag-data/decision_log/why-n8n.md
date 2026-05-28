# Decision: use n8n as orchestration layer

n8n was selected for the local MVP because it provides visual orchestration, webhooks, cron jobs, HTTP calls, and simple integration with Mattermost, Plane, Ollama, and the RAG backend.

Decision:

- Plane remains the source of truth.
- n8n remains the orchestration layer.
- Ollama remains the local inference layer.
- Mattermost remains the notification layer.
- RAG Backend owns retrieval and context assembly.

This keeps the MVP modular and allows replacing individual parts later.
