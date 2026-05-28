# AIGILE 0.1.0 System Summary

## Purpose

AIGILE is a local AI-native Delivery Operating System. It helps move from product work in Plane to AI review, local knowledge lookup, team communication, and eventually controlled implementation prompts.

## Runtime Components

- Plane: source of truth for work items.
- Mattermost: communication and AI notification layer.
- n8n: orchestration layer.
- Ollama: local LLM inference.
- Open WebUI: local model UI.
- Qdrant: vector database.
- RAG Backend: knowledge ingestion and retrieval.
- AIGILE Backend: Plane AI review, comment application, and task chat startup.
- Plane Pages Project Knowledge: approved Public pages with `[AI]` in the title are synced into the separate `plane_pages` RAG collection.

## Primary Flows

### Plane AI Review

1. User opens a Plane task.
2. User clicks `AI анализ`.
3. AIGILE backend fetches issue data.
4. AIGILE backend detects type from labels.
5. Agent set is selected by type.
6. Ollama returns structured agent reviews.
7. Plane renders review panel.
8. Review is saved to JSONL.
9. Label `AI-R` is added.

### AI Recommendation To Comments

1. User opens an agent result.
2. User clicks `Отправить в комментарии`.
3. AIGILE backend loads the saved review.
4. AIGILE backend formats a safe comment.
5. Plane issue comment is created.
6. Label `AI-A` is added.
7. Apply event is saved to JSONL.

### Task Chat Startup

1. User clicks `В Mattermost` after AI analysis.
2. AIGILE backend builds the task context graph.
3. AIGILE backend opens or reuses a direct Mattermost channel.
4. `aigile-agent` posts a task-focused message to the user.
5. Task chat context is saved to JSONL.

### Task Chat Threads

1. The first Mattermost task message becomes the thread root.
2. User replies to that root message.
3. AIGILE backend polls task threads locally.
4. AIGILE backend loads the saved task context and refreshes Plane context.
5. `aigile-agent` answers in the same Mattermost thread.
6. The agent uses approved Plane Pages from `plane_pages` as strict project knowledge when available.
7. Thread processing state is saved to JSON.

### Plane Pages RAG Sync

1. Admin creates or edits a Public Plane Page in `AIGILE Platform`.
2. The page title must contain `[AI]` to be approved for RAG.
3. Manual sync runs through `POST /api/sync-plane-pages`.
4. Daily refresh runs a separate Plane Pages sync step.
5. Approved pages are indexed into `plane_pages`.
6. Removed, archived, private, or unapproved pages are deleted from `plane_pages`.
7. `[AI] AIGILE Agent Rules` is bootstrapped as the default strict rules page.

### Chat To Plane Draft And Approval

1. User asks in a task thread to add or update task information.
2. AIGILE creates a pending draft and does not change Plane.
3. The draft is stored in the thread state.
4. User explicitly approves with `y` or `да`, or rejects with `n` or `нет`.
5. For `!ac`, AIGILE updates the task description `Acceptance Criteria` block and marks the added line with `[AI]`.
6. For `!ac`, AIGILE adds label `AIA` and leaves a short summary comment.
7. For other approved drafts, AIGILE writes a Plane comment and adds label `AI-A`.
7. AIGILE saves the action to AI apply history.
8. Task description is not overwritten.

### Task Context Graph

Task chat context includes:

- current task;
- parent chain;
- children;
- linked issues;
- cycles;
- modules;
- latest AI review.

This lets future agents answer questions with context from the parent epic and delivery structure.

## Safety Rules

- AI does not overwrite descriptions.
- AI recommendations go to comments first, except approved `!ac` task-chat updates, which update the controlled `Acceptance Criteria` block.
- Human review remains mandatory.
- Chat-to-Plane updates are reserved for a future explicit approval flow.
- No cloud AI APIs are used in this MVP.

## Version

Current local version: AIGILE 0.1.0.
