# AIGILE 0.1.0 Product Decisions

Date: 2026-05-27

## Decisions

### Plane Remains Source Of Truth

All work items, hierarchy, labels, states, modules, cycles, pages, and proposals live in Plane. AI can review and suggest, but Plane remains the delivery record.

### Labels Represent Work Item Types

Plane Community does not provide a reliable enterprise work item type model for this MVP. AIGILE uses labels such as Epic, Story, Task, Bug, RFC, Tech Debt, Research, Release, and Proposal as the type source of truth.

### AI Review Requires A Type Label

AI analysis is blocked without a type label. This prevents the system from routing an issue to the wrong agent set.

### AI Writes To Comments, Not Description

AI recommendations are added to Plane comments. This avoids overwriting user-authored task descriptions and keeps AI intervention visible.

### AI Intervention Is Marked With Labels

- `AI-R`: AI review was run.
- `AI-A`: AI added assistance into comments.

### Mattermost Task Chat Starts From Plane

The Plane review panel can start a Mattermost direct conversation. The conversation is task-scoped and initiated by local user `aigile-agent`.

### Task Context Graph Is Required

Agents must not answer only from a single task card when parent or linked work exists. AIGILE therefore stores parent, children, relations, cycle, module, and latest review context when task chat starts.

### No Chat-To-Plane Updates Yet

Updating Plane from Mattermost chat is intentionally postponed. The next version must add explicit approval before any task update.

### Release Notes Will Be Maintained

Starting with version 0.1.0, AIGILE maintains local release notes in `ai-delivery-app/rag-data/project_docs/aigile-release-notes.md`.
