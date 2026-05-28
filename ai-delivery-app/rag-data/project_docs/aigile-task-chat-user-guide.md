# AIGILE Task Chat User Guide

## Purpose

Task chat lets a user discuss a specific Plane task with `aigile-agent` in a Mattermost thread.

The first agent message is the task card. All task-specific questions should be written as replies in that thread.

## Basic Flow

1. Run `AI анализ` in Plane.
2. Click `В Mattermost`.
3. Open the Mattermost task thread.
4. Ask questions or request changes.
5. If the agent prepares a draft, approve or reject it.

## Approval Commands

Use short confirmations:

- `y` or `да` means approve and apply the pending draft.
- `n` or `нет` means reject and discard the pending draft.

The agent must not update Plane without `y` or `да`.

## Quick Commands

Use quick prefixes at the start of a message when you want the agent to prepare a structured task update.

### `!ac`

Acceptance criteria.

Example:

```text
!ac пользователь видит понятную ошибку при слабом пароле
```

The agent creates a pending acceptance-criteria draft. After `y` or `да`, it updates the Plane task description:

- finds or creates the `Acceptance Criteria` block;
- adds the approved line with `[AI]`;
- adds label `AIA`;
- leaves only a short summary comment.

If the agent has just suggested acceptance criteria, the user may also write `добавь их в задачу` or `добавь это в задачу`. The system resolves `их` / `это` from the previous agent message and creates an `!ac` draft with the actual criteria, not with the literal phrase.

### `!note`

General task note.

Example:

```text
!note проверить UX текст ошибки вместе с фронтендом
```

### `!risk`

Risk note.

Example:

```text
!risk ошибка авторизации может блокировать регистрацию в MVP
```

### `!dep`

Dependency note.

Example:

```text
!dep зависит от готовности backend endpoint для проверки пароля
```

### `!deadline`

Deadline note.

Example:

```text
!deadline нужно завершить до 16 июня 2026
```

## Safety Rules

- Drafts are not applied automatically.
- Approved `!ac` drafts update only the `Acceptance Criteria` block in the task description.
- The added AC line is marked with `[AI]`.
- Approved `!ac` drafts add label `AIA`.
- Other approved drafts are added as Plane comments and add label `AI-A`.
- Dialogue memory is stored per Mattermost thread.
- The agent uses task context: parent epic, linked tasks, module, cycle, and latest AI review.
