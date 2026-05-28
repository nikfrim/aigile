---
title: Plane Pages RAG Sync
tags:
  - plane
  - rag
  - project-knowledge
---

# Plane Pages RAG Sync

Plane Pages are used as strict project knowledge for AIGILE agents.

## Scope

MVP supports one Plane project:

- Workspace: `aigile`
- Project: `AIGILE Platform`
- Project identifier: `AIGILE`

## Approval Rule

A Plane Page is indexed only when all conditions are true:

- it belongs to `AIGILE Platform`;
- it is Public;
- its title contains `[AI]`;
- it is not archived;
- it is not deleted.

## Collection

Approved pages are indexed into:

```text
plane_pages
```

This keeps project operating documents separate from `knowledge_books`.

## Deletion Rule

If a previously indexed page is removed, archived, made private, or loses `[AI]` in the title, its chunks are removed from `plane_pages`.

## Versioning

Each chunk keeps page metadata:

- `plane_page_id`
- `plane_project_id`
- `plane_project_identifier`
- `updated_at`
- `content_sha256`
- `version`
- `source_path`

The version combines page `updated_at` and the content hash prefix.

## Manual Sync

Admin endpoint:

```http
POST /api/sync-plane-pages
```

Local URL:

```text
http://localhost:8091/api/sync-plane-pages
```

## Agent Rules Page

The sync bootstraps a default page:

```text
[AI] AIGILE Agent Rules
```

Agents treat this page as strict project knowledge for answer formats, review behavior, task-chat drafts, and comment rules.
