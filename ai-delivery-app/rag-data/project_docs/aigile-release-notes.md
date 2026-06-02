# AIGILE Release Notes

## 0.1.1 - Stabilization Pack

Date: 2026-06-02
Status: in progress

### Summary

0.1.1 starts as a stabilization release. The first artifact is a reusable smoke test pack so every future change can be checked against the same local MVP flow.

### Added

- Smoke test pack at `ai-delivery-app/rag-data/project_docs/aigile-smoke-test-pack.md`.
- Manual verification flow for service health, Plane AI Review Gate, Mattermost task threads, chat-to-Plane approval, RAG `/kb`, document upload ingestion, Plane Pages sync, and git safety.
- Local health dashboard at `http://localhost:8091/dashboard`.
- Machine-readable health endpoint at `http://localhost:8091/healthz`.
- Health checks for AIGILE backend, Plane database, Plane web, Mattermost, n8n, Ollama, RAG backend, Qdrant, and Open WebUI.
- Task Chat MVP 2 helpers:
  - `!help` command in Mattermost task threads;
  - `!status` command showing task-chat memory and pending draft state;
  - test coverage for `!risk`, `!dep`, and `!deadline` command routing.
- Plane Pages knowledge templates:
  - `[AI] Bug Template`;
  - `[AI] Story Template`;
  - `[AI] Epic Template`;
  - `[AI] Agent Response Rules`.
- AI Review Gate and Mattermost task chat now explicitly request the relevant type template and response rules from the separate `plane_pages` RAG collection.

### Next

- Turn the manual smoke pack into a more automated status check where practical.

## 0.1.0 - Local AI Delivery MVP

Date: 2026-05-27
Status: local MVP

### Summary

AIGILE 0.1.0 is the first usable local AI delivery platform baseline. It connects Plane, Mattermost, n8n, Ollama, Open WebUI, Qdrant, and the AIGILE backend into a local delivery workspace with AI review, RAG knowledge, task chat startup, and demo data.

### Added

- Local Docker-based AIGILE runtime on Windows + Docker Desktop.
- Plane Community as the work item source of truth.
- Mattermost as notification and communication layer.
- n8n workflows for AI delivery events, RAG refresh, Mattermost RAG ingestion, and `/kb`.
- Ollama with local model `qwen2.5-coder:7b-instruct`.
- Open WebUI for local model interaction.
- Qdrant as local vector database.
- RAG Backend with collection-based knowledge architecture.
- Local RAG collections:
  - knowledge_books
  - project_docs
  - technical_docs
  - team_context
  - decision_log
  - prompt_registry
- Mattermost knowledge ingestion from configured technical channel.
- Text, Markdown, JSON, and text PDF ingestion into RAG.
- `/kb` Mattermost command for knowledge questions.
- Short conversational follow-up memory for `/kb`.
- Plane `AI анализ` button.
- AI Review Gate with agent routing by type label.
- Green/yellow/red review status.
- Deterministic quality gate for underspecified tasks.
- `AI-R` label after AI review.
- `Отправить в комментарии` action for AI recommendations.
- `AI-A` label after AI recommendation is added to comments.
- Review and apply history logs.
- Mattermost task chat startup from Plane review panel.
- Local `aigile-agent` Mattermost user for task chat messages.
- Task context graph with parent, children, relations, cycles, modules, and latest review.
- AI Agile demo dataset in Plane for testing:
  - 3 epics;
  - 15 SDLC work items;
  - 3 proposals;
  - cycles;
  - modules;
  - views;
  - pages;
  - intake.

### Changed

- Plane architecture was corrected: Epic, Feature, Task, Bug, and RFC are no longer modeled as separate projects.
- Work item type detection now uses labels because Plane Community does not provide a reliable custom type layer for this MVP.
- AI recommendations are written to comments, not descriptions, to avoid overwriting user-authored task content.
- Mattermost task communication is represented as agent conversation linked to a Plane task, not as raw notifications only.

### Fixed

- Fixed Plane AI button choosing parent issue key instead of the currently opened issue.
- Improved missing type label error into a user-friendly blocked-analysis message.
- Changed AI comment action label from `Добавить замечание` to `Отправить в комментарии`.
- Ensured the task chat message is sent from `aigile-agent`, not from the human admin account.

### Known Limitations

- Mattermost task chat follow-up messages are not processed yet.
- Chat-to-Plane updates are not implemented yet.
- RAG knowledge has a solid local base but still needs more project documents.
- Some advanced Plane enterprise concepts are approximated with labels, views, modules, pages, and intake.

### Smoke Test

1. Open Plane at `http://localhost:8080`.
2. Open any demo task, for example `AIGILE-16`.
3. Run `AI анализ`.
4. Confirm the review panel appears.
5. Confirm status, detected type, and agents are visible.
6. Click `Отправить в комментарии` on an agent recommendation.
7. Confirm a Plane comment is added and label `AI-A` exists.
8. Click `В Mattermost`.
9. Open Mattermost at `http://localhost:8065`.
10. Confirm a direct message from `aigile-agent`.
11. Confirm the message includes parent/module/cycle/related issue context.
12. In Mattermost, run `/kb <question>` and confirm it answers from local knowledge.

### Next Candidate Version

Potential `0.2.0` scope:

- Mattermost task-chat listener.
- Conversational replies from `aigile-agent`.
- Task-specific chat memory.
- Approval-gated updates from Mattermost back into Plane comments or fields.
- Better RAG context injection into task chat answers.
