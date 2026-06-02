# AIGILE Feature List

Version: 0.1.0
Updated: 2026-05-27

## Product Baseline

- Fully local AI delivery platform on Windows + Docker Desktop.
- Plane is the source of truth for product and delivery work.
- Mattermost is the team communication and notification layer.
- n8n is the orchestration layer for webhooks, RAG refresh, and Mattermost commands.
- Ollama is the local inference layer.
- Qdrant is the local vector database.
- RAG Backend stores and queries local knowledge collections.
- Notion is used only as external planning/documentation, not as runtime infrastructure.

## Smoke Test Pack

- A reusable smoke test pack exists at `ai-delivery-app/rag-data/project_docs/aigile-smoke-test-pack.md`.
- The pack is the standard manual verification checklist after AIGILE changes.
- It covers service health, Plane AI Review Gate, Mattermost task threads, chat-to-Plane approval, `/kb`, document upload ingestion, Plane Pages sync, and git safety checks.
- It is intended to become the basis for a future health dashboard and automated smoke status.

## Plane Structure

- Projects were corrected from wrong type-based projects to domain-oriented projects:
  - AIGILE Platform
  - AI Agents
  - Infrastructure
  - Research Lab
- Work item types are represented through labels in Plane Community:
  - Epic
  - Feature
  - Story
  - Task
  - Bug
  - RFC
  - Spike
  - Tech Debt
  - Research
  - Release
  - Proposal
- Delivery states are configured for backlog, discovery, refinement, ready, in progress, review, testing, done, and released.
- RFC and bug-oriented workflow states are available.
- Labels exist for domains such as backend, frontend, ai-agent, infrastructure, rag, ux, api, security, delivery, discovery, and tech-debt.

## AI Review Gate

- Plane has an embedded `AI анализ` button on the issue page.
- The button calls AIGILE backend directly.
- AI analysis is blocked unless the issue has a type label.
- Type labels route the issue to the right agent set.
- Agents return structured review results with green/yellow/red status.
- The Plane panel shows:
  - overall status;
  - detected type;
  - agent list;
  - summaries;
  - findings;
  - recommendations.
- A deterministic gate marks title-only or underspecified issues as red/yellow before delivery.
- Running AI analysis adds label `AI-R`.
- Review history is saved to `ai-delivery-app/logs/ai-review-history.jsonl`.

## AI Comments

- Agent recommendations are not written into the issue description.
- The user can send recommendations into Plane comments via the `Отправить в комментарии` button.
- Applying a recommendation creates a Plane issue comment.
- Applying a recommendation adds label `AI-A`.
- Apply history is saved to `ai-delivery-app/logs/ai-apply-history.jsonl`.
- AI changes are visible and require human review.

## Mattermost Task Chat

- After AI analysis, the Plane AI Review panel includes a `В Mattermost` action.
- The action starts a direct Mattermost conversation for the current task.
- Messages are sent from local user `aigile-agent`.
- The first message contains:
  - issue key;
  - issue title;
  - detected type;
  - AI review status;
  - Plane link;
  - agent summaries;
  - related context around the task.
- Task chat context is saved to `ai-delivery-app/logs/task-chat-context.jsonl`.

## Mattermost Task Chat Threads

- The first Mattermost message is treated as the root card for a Plane task.
- All follow-up discussion happens as replies in that Mattermost thread.
- AIGILE backend polls task threads locally and replies from `aigile-agent`.
- The agent resolves the thread by `root_id`, loads saved task context, refreshes Plane context, and answers in the same thread.
- Dialogue history is stored per thread in `dialogue_history`.
- If the user asks to change the task, AIGILE creates a pending draft instead of changing Plane immediately.
- Pending drafts are stored in the thread state as `pending_draft`.
- Approval commands `y` or `да` apply the pending draft.
- Approved non-AC drafts are written to Plane as comments, not into the task description.
- Approved `!ac` drafts update the task description `Acceptance Criteria` block, add the approved line with `[AI]`, add the `AIA` label, and leave a short summary comment.
- Follow-up phrases like `добавь их в задачу` are resolved from the previous agent message when it contains acceptance criteria.
- Approved drafts are saved to AI apply history.
- Cancellation commands `n` or `нет` discard the pending draft.
- Quick task-chat prefixes are supported: `!ac`, `!note`, `!risk`, `!dep`, `!deadline`.
- Old or inaccessible threads are skipped safely and do not break the poller.
- Thread processing state is stored in `ai-delivery-app/logs/task-chat-thread-state.json`.
- Current safety rule: chat can discuss and propose Plane changes, but does not update Plane without a future approval flow.

## Task Context Graph

When a task chat starts, AIGILE builds a context graph:

- current issue;
- parent chain;
- children;
- outgoing and incoming issue relations;
- cycles;
- modules;
- latest AI review.

This lets the agent understand that a task belongs to an epic, module, cycle, or linked delivery chain.

Example:

- `AIGILE-16` is a bug.
- Parent: `AIGILE-10 MVP: Регистрация`.
- Module: `Auth & Identity`.
- Cycle: `MVP Release Hardening`.
- Related issue: `AIGILE-13 MVP: Story - регистрация по email`.

## RAG Foundation

- RAG Backend endpoints:
  - `GET /health`
  - `POST /rag/ingest`
  - `POST /rag/query`
  - `POST /rag/analyze-issue`
  - `POST /context/search`
  - `POST /collections/list`
- RAG collections:
  - knowledge_books
  - project_docs
  - plane_pages
  - technical_docs
  - team_context
  - decision_log
  - prompt_registry
- RAG documents live in `ai-delivery-app/rag-data`.
- Supported ingestion file types include `.md`, `.txt`, `.json`, and text PDFs.
- Mattermost `knowledge-books` channel can ingest documents into RAG.
- Mattermost `/kb` command answers from the knowledge base.
- `/kb` supports short conversational follow-ups by channel and user context.

## Plane Pages Project Knowledge

- Plane Pages are now a first-class project knowledge source.
- MVP sync supports the single project `AIGILE Platform`.
- Only Public pages with `[AI]` in the title are indexed.
- Indexed pages go to the separate `plane_pages` collection and are not mixed with `knowledge_books`.
- Removed, archived, private, or unapproved pages are deleted from `plane_pages`.
- `[AI] AIGILE Agent Rules` is bootstrapped as the default strict rules page.
- Admin can trigger sync manually through `POST /api/sync-plane-pages`.
- Daily refresh includes a separate Plane Pages sync step.
- AI Review Gate and Mattermost task chat use `plane_pages` as strict project knowledge.
- AI Review Gate queries `plane_pages` with task type and selected agent names so per-agent rules are retrieved.
- Mattermost task chat queries `plane_pages` with `Task Chat Agent`, `AIGILE Agent Rules`, and task-thread context so approved page rules guide answers and drafts.

## Demo Data

Plane contains an AI Agile site MVP demo dataset:

- Epics:
  - MVP: Регистрация
  - MVP: Оставить контакты
  - MVP: Onepage лендинг
- 15 SDLC work items across Story, Task, Bug, RFC, Feature, Tech Debt, Research, and Release.
- 3 Proposal items.
- Cycles:
  - MVP Discovery Sprint
  - MVP Build Sprint
  - MVP Release Hardening
- Modules:
  - Auth & Identity
  - Lead Capture
  - Marketing Landing
  - Release & QA
- Views:
  - MVP Executive Dashboard
  - MVP Bug Triage
  - AI Review Gate Queue
  - MVP Release Board
- Pages:
  - MVP Product Brief - AI Agile Site
  - Registration Flow Notes
  - Lead Capture Runbook
  - MVP Release Readiness Checklist
- Intake:
  - MVP Ideas Intake

## Current Limitations

- Mattermost task chat starts a conversation but does not yet process follow-up user messages.
- Chat-to-Plane updates are intentionally not implemented yet.
- AI never updates Plane fields from chat without a future explicit approval flow.
- Plane Community has no reliable custom work item type layer, so labels are the current type source of truth.
