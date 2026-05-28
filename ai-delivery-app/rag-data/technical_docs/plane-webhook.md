---
title: Plane webhook local MVP
tags: [plane, webhook, n8n, local]
---

# Plane webhook local MVP

Plane is the source of truth for delivery work items.

The current MVP sends Plane issue events into n8n. n8n orchestrates analysis and posts short notifications into Mattermost.

The RAG extension keeps n8n as the orchestration layer and adds AIGILE RAG Backend for local context retrieval.

Runtime flow:

Plane -> n8n -> AIGILE RAG Backend -> Qdrant collections -> Ollama -> n8n -> Mattermost.

The system must stay fully local and must not call cloud APIs.
