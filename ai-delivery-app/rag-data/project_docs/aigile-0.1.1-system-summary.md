# AIGILE 0.1.1-dev System Summary

Updated: 2026-06-03

## Purpose

AIGILE is a fully local AI-assisted Delivery Operating System for a Product / Delivery Manager.

It connects Plane work items, Mattermost task discussions, AI Review, local RAG knowledge, delivery signals, and management dashboards into one local demo-ready system.

The system is designed to show how AI can improve product delivery processes without cloud AI APIs.

## Runtime Components

- Plane: source of truth for work items.
- Mattermost: communication layer, task threads, and AI agent conversations.
- n8n: orchestration layer for webhooks, RAG refresh, and scheduled flows.
- Ollama: local LLM inference.
- Open WebUI: local model UI.
- Qdrant: vector database.
- RAG Backend: knowledge ingestion and retrieval.
- AIGILE Backend: AI Review Gate, task chat, delivery signals, dashboards, demo seed/reset.
- Plane Pages Project Knowledge: approved Public pages with `[AI]` in the title are synced into the separate `plane_pages` RAG collection.
- Notion: external project documentation, not a runtime component.

## Primary Product Flows

### Health Check

1. User opens `http://localhost:8091/dashboard`.
2. AIGILE checks local services.
3. Dashboard shows whether the demo/runtime is ready.

Services checked:

- AIGILE backend;
- Plane database;
- Plane web;
- Mattermost;
- n8n;
- Ollama;
- RAG backend;
- Qdrant;
- Open WebUI.

### Plane AI Review Gate

1. User opens a Plane task.
2. User clicks `AI анализ`.
3. AIGILE backend fetches issue data.
4. AIGILE detects task type from labels.
5. Agent Router selects agents by task type.
6. Ollama returns structured agent reviews.
7. Plane renders the review panel with green/yellow/red statuses.
8. Review is saved to JSONL.
9. Label `AI-R` is added.

AI Review is blocked when the task has no type label.

### AI Recommendation To Comments

1. User opens an agent result.
2. User clicks `Отправить в комментарии`.
3. AIGILE loads the saved review.
4. AIGILE formats a safe comment.
5. Plane issue comment is created.
6. Label `AI-A` is added.
7. Apply event is saved to JSONL.

### Mattermost Task Thread

1. User clicks `В Mattermost` after AI analysis.
2. AIGILE builds the task context graph.
3. AIGILE opens or reuses a direct Mattermost channel.
4. `aigile-agent` posts a task-focused message.
5. The first message becomes the task thread root.
6. User replies inside the thread.
7. AIGILE answers in the same thread with task context.

Task context includes:

- current task;
- parent chain;
- children;
- linked issues;
- cycles;
- modules;
- latest AI Review;
- thread dialogue memory.

### Chat To Plane Draft And Approval

1. User asks the agent to add or update task information.
2. AIGILE creates a pending draft.
3. Plane is not changed yet.
4. User approves with `y` or `да`, or rejects with `n` or `нет`.
5. For `!ac`, AIGILE updates only the `Acceptance Criteria` block and marks the added line with `[AI]`.
6. For `!ac`, AIGILE adds label `AIA` and leaves a short summary comment.
7. For other approved drafts, AIGILE writes a Plane comment and adds label `AI-A`.
8. AIGILE saves the action to AI apply history.

### Delivery Signals From Mattermost Threads

Mattermost task threads can capture delivery management signals without changing Plane.

Commands:

- `!risk [low|medium|high|critical] text`;
- `!blocker text`;
- `!dep text`;
- `!decision text`;
- `!question text`;
- `!action text`.

Delivery Signal structure:

- id;
- type;
- severity;
- source;
- related issue key/id;
- module when available;
- text summary;
- suggested action;
- created_at;
- status: `open`, `acknowledged`, `resolved`.

Signals are saved to:

```text
ai-delivery-app/logs/delivery-signals.jsonl
```

### Delivery Intelligence Dashboard

Dashboard:

```text
http://localhost:8091/delivery-dashboard
```

JSON:

```text
http://localhost:8091/api/delivery-intelligence
```

Dashboard shows:

- overall delivery status;
- reviewed vs unreviewed tasks;
- green/yellow/red AI Review counts;
- top risks;
- blockers;
- requirement quality signals;
- module signals;
- decisions needed;
- open questions;
- action items;
- suggested actions.

It uses real available signals:

- Plane issues;
- AI Review history;
- AI apply history;
- Mattermost task thread memory;
- Delivery Signals.

### Daily Delivery Brief

Daily Brief:

```text
http://localhost:8091/daily-delivery-brief
```

JSON:

```text
http://localhost:8091/api/daily-delivery-brief
```

Mattermost send endpoint:

```text
POST /api/daily-delivery-brief/send
```

Daily Brief shows:

- Date;
- Project Health Index;
- Schedule Confidence;
- Executive / Kanban / Risks / Team Signals / Data Quality modes;
- infographic summary cards;
- Kanban flow metrics with expandable dynamic explanations:
  - throughput;
  - lead time;
  - cycle time;
  - WIP;
  - blocked time;
  - flow efficiency;
  - WIP aging;
  - rework rate;
- Overall Status;
- Executive Summary;
- AI-ranked Top 5 Risks;
- Top Blockers;
- Decisions Needed;
- Requirement Quality Issues;
- Changes Since Yesterday;
- Suggested Actions for Today;
- Data Notes.

The brief is data-honest. It does not invent facts. If a source has no data, the brief says so explicitly.

Project Health Index is intentionally simple and explainable for a live manager demo. It reacts to blockers, red/yellow AI reviews, weak requirements, missing task types, open Mattermost delivery signals, and top risks. Schedule Confidence is derived from the same signal set and should be treated as an attention indicator, not as a formal project forecast.

Examples:

- `No critical risks found in available data.`
- `No meeting/thread signals available.`
- `Historical comparison is not available yet.`

### Plane Pages RAG Sync

1. Admin creates or edits a Public Plane Page in `AIGILE Platform`.
2. Page title must contain `[AI]` to be approved for RAG.
3. Manual sync runs through `POST /api/sync-plane-pages`.
4. Daily refresh runs a separate Plane Pages sync step.
5. Approved pages are indexed into `plane_pages`.
6. Removed, archived, private, or unapproved pages are deleted from `plane_pages`.
7. `[AI] AIGILE Agent Rules` is bootstrapped as the default strict rules page.

### Live Demo Seed / Reset

Commands:

```powershell
.\seed-demo.ps1
.\reset-demo.ps1
```

Backend endpoints:

```text
POST /api/demo/seed
POST /api/demo/reset
```

Demo tasks are marked with:

- label: `AIGILE-DEMO`;
- title prefix: `[DEMO]`.

Demo tasks are intentionally imperfect so AI Review returns useful yellow/red findings.

## Safety Rules

- AI does not overwrite descriptions.
- AI recommendations go to comments first.
- Approved `!ac` updates only the controlled `Acceptance Criteria` block.
- Human approval remains mandatory.
- Delivery signal commands do not modify Plane.
- Missing data is reported explicitly.
- No cloud AI APIs are used in this MVP.
- Existing Plane, Mattermost, n8n, RAG, and AI Review flows must not be broken by dashboard/report features.

## Version

Current local version: AIGILE 0.1.1-dev.
