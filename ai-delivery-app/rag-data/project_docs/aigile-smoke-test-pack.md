# AIGILE Smoke Test Pack

Version: 0.1.1-dev
Updated: 2026-06-02
Owner: AIGILE
Status: active checklist

## Purpose

This checklist is the standard verification pack after any change in AIGILE.

It protects the working local MVP:

```text
Plane -> AIGILE backend / n8n -> Ollama / RAG -> Mattermost -> Plane
```

The goal is not to test every edge case. The goal is to quickly answer:

- Are all local services alive?
- Can Plane still start AI review?
- Can Mattermost task chat still work in a thread?
- Can approved chat changes update Plane safely?
- Can RAG still ingest and answer from local knowledge?

## When To Run

Run this pack:

- after backend changes;
- after Plane UI patch changes;
- after Mattermost integration changes;
- after RAG backend changes;
- after Docker Compose changes;
- before tagging a local release;
- when something feels broken after a PC or Docker restart.

## Test Data

Recommended demo issues:

- `AIGILE-10` - parent registration epic.
- `AIGILE-16` - bug under the registration epic.
- Any task with a type label: `Epic`, `Story`, `Bug`, `Task`, `Tech Debt`, `Research`, or `Release`.

If a new issue is used, add exactly one type label before running AI analysis.

## 0. Service Health

Expected result: every service needed for the MVP is reachable.

Manual checks:

1. Open Plane: `http://localhost:8080`
2. Open Mattermost: `http://localhost:8065`
3. Open n8n: `http://localhost:5678`
4. Open Open WebUI: `http://localhost:3001`

Backend checks:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8091/health"
Invoke-RestMethod -Uri "http://127.0.0.1:8092/health"
Invoke-RestMethod -Uri "http://127.0.0.1:6333/collections"
```

Pass:

- AIGILE backend returns healthy status.
- RAG backend returns healthy status.
- Qdrant returns a collections response.

Fail:

- A service is unreachable.
- Docker Desktop is stopped.
- AIGILE backend or RAG backend is not running.

## 1. Plane AI Review Gate

Expected result: AI review starts only when the issue has a type label and then shows a review panel.

Steps:

1. Open Plane.
2. Open an issue, for example `AIGILE-16`.
3. Confirm it has one type label, for example `Bug`.
4. Click `AI анализ`.
5. Wait for the AI review panel.

Pass:

- The button does not jump or duplicate.
- The panel appears inside the issue detail area.
- The detected type matches the label.
- The agent list matches the issue type.
- The status is green, yellow, or red.
- The issue receives label `AI-R`.

Fail:

- The button is missing.
- The button flickers between locations.
- The review uses the parent issue type instead of the opened issue type.
- The review starts without a type label.
- The UI shows a technical stack trace.

## 2. Missing Type Label Guard

Expected result: AI analysis is blocked with a user-friendly message if the issue has no type label.

Steps:

1. Create or open an issue without type labels.
2. Click `AI анализ`.

Pass:

- The panel says analysis is blocked.
- The message tells the user to choose one type label:
  `Epic`, `Story`, `Bug`, `Task`, `Tech Debt`, `Research`, or `Release`.
- No AI review is started.

Fail:

- The system falls back to a random type.
- The system uses a parent label.
- The message is technical or unclear.

## 3. Send Review To Mattermost

Expected result: after AI review, the task card is sent to Mattermost and the conversation happens in a thread.

Steps:

1. Run AI review on a task.
2. Click `В Mattermost`.
3. Open Mattermost.
4. Find the direct message or task thread from `aigile-agent`.

Pass:

- The message is sent by `aigile-agent`.
- The message contains:
  - issue key;
  - issue title;
  - type;
  - AI review status;
  - Plane link;
  - agent summaries;
  - parent/module/cycle/related context when available.
- The Plane link opens the specific issue, not only the board.
- Follow-up discussion happens as replies in the same thread.

Fail:

- The button does nothing.
- The message is posted by the human admin account.
- The link opens only the issue board.
- Replies are created as separate channel messages instead of thread replies.

## 4. Mattermost Task Chat Memory

Expected result: the task agent answers in the same thread and remembers the task context.

Steps:

1. Reply in the task thread:

```text
в родительской задаче какие требования?
```

2. Wait for `aigile-agent` response.

Pass:

- The agent replies in the same thread.
- The answer uses task context.
- If a parent issue exists, the answer mentions it.
- The answer is not a generic response detached from the task.

Fail:

- The agent replies in a new channel message.
- The agent does not know which task is being discussed.
- The agent exposes technical logs or stack traces.

## 5. Chat To Plane Draft And Approval

Expected result: the agent prepares a draft, asks for approval, and only then updates Plane.

Steps:

1. Reply in the task thread:

```text
!ac добавь критерий: пользователь видит понятную ошибку при неверном пароле
```

2. Wait for the draft.
3. Reply:

```text
y
```

Alternative approval:

```text
да
```

Pass:

- The agent first creates a draft and does not update Plane immediately.
- After approval, the issue description contains an `Acceptance Criteria` block if it did not exist.
- The new line is marked with `[AI]`.
- The issue receives label `AIA`.
- Plane gets a short summary comment with:
  - user request;
  - action performed;
  - no huge duplicated AI text.

Fail:

- Plane changes before `y` or `да`.
- The original description is overwritten.
- The update is added to the wrong issue.
- The comment floods the issue with the full conversation.

## 6. Reject Draft

Expected result: `n` or `нет` cancels the pending draft.

Steps:

1. Ask the task agent for a small task change.
2. When the draft appears, reply:

```text
n
```

Alternative rejection:

```text
нет
```

Pass:

- The draft is discarded.
- Plane is not changed.
- The agent confirms cancellation.

Fail:

- Plane changes after rejection.
- The old draft is accidentally applied by a later unrelated message.

## 7. RAG Knowledge Query

Expected result: `/kb` answers from local knowledge.

Steps:

1. Open any Mattermost channel.
2. Run:

```text
/kb что такое Agile?
```

Pass:

- The command answers in Mattermost.
- The answer is short and useful.
- If context is used, it is shown in a readable format.
- If there is not enough data, the agent says that information is insufficient.

Fail:

- The command fails.
- The answer exposes raw `<details>` tags or technical stack traces.
- The answer pretends to know something when RAG has no relevant data.

## 8. RAG Document Upload

Expected result: documents uploaded to the allowed Mattermost knowledge channel are ingested into the right RAG collection.

Steps:

1. Open the configured Mattermost knowledge channel.
2. Upload a small `.txt`, `.md`, or text PDF file.
3. Wait for the ingestion result message.
4. Ask `/kb` a question based on that document.

Pass:

- Mattermost shows a short success message.
- Duplicate upload is detected and does not create repeated knowledge.
- `/kb` can answer from the uploaded document.

Fail:

- Upload from a normal channel is ingested.
- Duplicate files are loaded again as new knowledge.
- File parsing errors are shown as technical stack traces.

## 9. Plane Pages Project Knowledge

Expected result: Public Plane Pages with `[AI]` in the title are synced to the separate `plane_pages` RAG collection.

Steps:

1. Create or open a Plane Page in `AIGILE Platform`.
2. Make sure the page title starts with `[AI]`.
3. Make sure the page is Public.
4. Trigger manual sync:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8091/api/sync-plane-pages" -Method Post -ContentType "application/json" -Body "{}"
```

5. Query `plane_pages`:

```powershell
$body = @{ collection = "plane_pages"; query = "AIGILE Agent Rules"; limit = 3 } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8092/rag/query" -Method Post -ContentType "application/json" -Body $body
```

Pass:

- Approved Public `[AI]` pages appear in `plane_pages`.
- Non-public or non-`[AI]` pages are not indexed.
- Removed or unapproved pages are deleted from `plane_pages`.

Fail:

- Plane Pages are mixed into `knowledge_books`.
- Private pages are indexed.
- Removed pages remain searchable.

## 10. Regression Safety

Before declaring the change done, confirm:

- Existing Plane tasks were not deleted.
- Existing Mattermost channels still work.
- Existing RAG collections still exist.
- Existing n8n workflows were not removed.
- No `.env`, tokens, cookies, local sqlite databases, logs, or runtime state files were committed.
- `git status` only contains intentional changes.

## Quick Pass Definition

A change passes smoke testing when:

- service health checks pass;
- AI review works for a typed Plane issue;
- missing type label is blocked clearly;
- Mattermost task thread is created;
- task chat can answer in the thread;
- approval `y` or `да` applies only the intended draft;
- rejection `n` or `нет` applies nothing;
- `/kb` answers from local RAG;
- Plane Pages sync still works.

## Current Known Gaps

- This pack is manual-first.
- The next step should add a small health dashboard and one-click smoke status.
- n8n workflow import/activation should still be checked manually if workflow JSON changes.

