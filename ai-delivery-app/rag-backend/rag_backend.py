import base64
import hashlib
import io
import json
import math
import os
import re
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError


COLLECTIONS = [
    "knowledge_books",
    "project_docs",
    "plane_pages",
    "technical_docs",
    "team_context",
    "decision_log",
    "prompt_registry",
]

COLLECTION_ALIASES = {
    "knowledgebase": "knowledge_books",
    "knowledge_books": "knowledge_books",
    "project": "project_docs",
    "project_docs": "project_docs",
    "pages": "plane_pages",
    "plane_pages": "plane_pages",
    "project_pages": "plane_pages",
    "technical": "technical_docs",
    "technical_docs": "technical_docs",
    "team": "team_context",
    "team_context": "team_context",
    "decision": "decision_log",
    "decision_log": "decision_log",
    "prompts": "prompt_registry",
    "prompt_registry": "prompt_registry",
}

PORT = int(os.environ.get("RAG_BACKEND_PORT", "8092"))
DATA_ROOT = Path(os.environ.get("RAG_DATA_ROOT", "/data/rag-data"))
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b-instruct")
EMBEDDING_PROVIDER = os.environ.get("RAG_EMBEDDING_PROVIDER", "local-hash")
EMBEDDING_MODEL = os.environ.get("RAG_EMBEDDING_MODEL", "nomic-embed-text")
VECTOR_SIZE = int(os.environ.get("RAG_VECTOR_SIZE", "384"))
CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE", "1400"))
CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "180"))
TOP_K = int(os.environ.get("RAG_TOP_K", "5"))
MATTERMOST_URL = os.environ.get("MATTERMOST_INTERNAL_URL", "http://mattermost:8065").rstrip("/")
MATTERMOST_TOKEN = os.environ.get("MATTERMOST_BOT_TOKEN", "")
MATTERMOST_RAG_INGEST_ENABLED = os.environ.get("MATTERMOST_RAG_INGEST_ENABLED", "false").lower() == "true"
MATTERMOST_RAG_CHANNELS_JSON = os.environ.get("MATTERMOST_RAG_CHANNELS_JSON", "{\"channels\":{}}")
POLL_STATE_PATH = Path(os.environ.get("MATTERMOST_RAG_POLL_STATE_PATH", "/data/rag-data/.state/mattermost-rag-poll.json"))
CONVERSATION_STATE_PATH = Path(os.environ.get("RAG_CONVERSATION_STATE_PATH", "/data/rag-data/.state/kb-conversations.json"))
CONVERSATION_MAX_TURNS = int(os.environ.get("RAG_CONVERSATION_MAX_TURNS", "8"))


def normalize_collection(collection: str | None) -> str | None:
    if not collection:
        return None
    return COLLECTION_ALIASES.get(collection.strip(), collection.strip())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def respond(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 120,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Content-Type": "application/json; charset=utf-8"}
    request_headers.update(headers or {})
    req = request.Request(url, data=data, method=method, headers=request_headers)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot reach {url}: {exc.reason}") from exc


def http_bytes(method: str, url: str, headers: dict[str, str] | None = None, timeout: int = 120) -> tuple[bytes, dict[str, str]]:
    req = request.Request(url, method=method, headers=headers or {})
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.read(), dict(response.headers)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot reach {url}: {exc.reason}") from exc


def qdrant(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return http_json(method, f"{QDRANT_URL}{path}", payload, timeout=60)


def ensure_collection(collection: str) -> None:
    try:
        qdrant("GET", f"/collections/{collection}")
        return
    except RuntimeError:
        pass

    qdrant(
        "PUT",
        f"/collections/{collection}",
        {
            "vectors": {
                "size": VECTOR_SIZE,
                "distance": "Cosine",
            }
        },
    )


def ensure_collections() -> None:
    for collection in COLLECTIONS:
        ensure_collection(collection)


def local_hash_embedding(text: str) -> list[float]:
    vector = [0.0] * VECTOR_SIZE
    tokens = tokenize(text)
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % VECTOR_SIZE
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def ollama_embedding(text: str) -> list[float]:
    result = http_json(
        "POST",
        f"{OLLAMA_BASE_URL}/api/embeddings",
        {"model": EMBEDDING_MODEL, "prompt": text[:8000]},
        timeout=120,
    )
    embedding = result.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise RuntimeError("Ollama embeddings response did not contain embedding")
    if len(embedding) == VECTOR_SIZE:
        return embedding
    if len(embedding) > VECTOR_SIZE:
        trimmed = embedding[:VECTOR_SIZE]
        norm = math.sqrt(sum(value * value for value in trimmed)) or 1.0
        return [value / norm for value in trimmed]
    padded = embedding + [0.0] * (VECTOR_SIZE - len(embedding))
    norm = math.sqrt(sum(value * value for value in padded)) or 1.0
    return [value / norm for value in padded]


def embed(text: str) -> list[float]:
    if EMBEDDING_PROVIDER == "ollama":
        try:
            return ollama_embedding(text)
        except Exception:
            return local_hash_embedding(text)
    return local_hash_embedding(text)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[\w\-\u0430-\u044f\u0410-\u042f\u0451\u0401]+", text.lower(), flags=re.UNICODE)


def lexical_score(query: str, text: str, title: str | None = None) -> float:
    query_tokens = {token for token in tokenize(query) if len(token) > 1}
    if not query_tokens:
        return 0.0
    haystack_tokens = set(tokenize(f"{title or ''}\n{text}"))
    overlap = len(query_tokens & haystack_tokens)
    return overlap / max(len(query_tokens), 1)


def read_document(path: Path) -> tuple[str, dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    metadata: dict[str, Any] = {}
    text = raw

    if path.suffix.lower() == ".json":
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
            body = parsed.get("text") or parsed.get("content") or parsed.get("body")
            text = body if isinstance(body, str) else json.dumps(parsed, ensure_ascii=False, indent=2)
        else:
            text = json.dumps(parsed, ensure_ascii=False, indent=2)

    return text, metadata


def chunk_text(text: str) -> list[str]:
    cleaned = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not cleaned:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + CHUNK_SIZE)
        if end < len(cleaned):
            boundary = max(cleaned.rfind("\n\n", start, end), cleaned.rfind(". ", start, end))
            if boundary > start + CHUNK_SIZE // 2:
                end = boundary + 1
        chunks.append(cleaned[start:end].strip())
        if end >= len(cleaned):
            break
        start = max(0, end - CHUNK_OVERLAP)
    return [chunk for chunk in chunks if chunk]


def metadata_for(path: Path, collection: str, override: dict[str, Any]) -> dict[str, Any]:
    stat = path.stat()
    base = {
        "source_type": path.suffix.lower().lstrip(".") or "text",
        "collection": collection,
        "title": path.stem,
        "author": "local",
        "project": "AIGILE",
        "tags": [],
        "created_at": datetime.fromtimestamp(stat.st_ctime, timezone.utc).isoformat(),
        "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "version": "1",
        "language": "ru",
        "access_level": "local",
        "source_path": str(path.relative_to(DATA_ROOT)).replace("\\", "/"),
    }
    base.update({key: value for key, value in override.items() if value is not None})
    base["collection"] = collection
    base["source_path"] = str(path.relative_to(DATA_ROOT)).replace("\\", "/")
    return base


def point_id(collection: str, source_path: str, chunk_index: int, content: str) -> str:
    raw = f"{collection}:{source_path}:{chunk_index}:{hashlib.sha1(content.encode('utf-8')).hexdigest()}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))


def document_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def find_existing_document_by_hash(collection: str, content_sha256: str) -> dict[str, Any] | None:
    if not content_sha256:
        return None
    ensure_collection(collection)
    result = qdrant(
        "POST",
        f"/collections/{collection}/points/scroll",
        {
            "limit": 1,
            "with_payload": True,
            "with_vector": False,
            "filter": {
                "must": [
                    {"key": "content_sha256", "match": {"value": content_sha256}},
                ],
            },
        },
    )
    points = result.get("result", {}).get("points") or []
    if not points:
        return None
    payload = points[0].get("payload") or {}
    return {
        "source_path": payload.get("source_path"),
        "title": payload.get("title"),
        "mattermost_file_id": payload.get("mattermost_file_id"),
    }


def qdrant_match_filter(filters: dict[str, Any]) -> dict[str, Any]:
    must = []
    for key, value in filters.items():
        if value is not None:
            must.append({"key": key, "match": {"value": value}})
    return {"must": must} if must else {}


def delete_points_by_filter(collection: str, filters: dict[str, Any]) -> dict[str, Any]:
    collection = normalize_collection(collection) or ""
    if collection not in COLLECTIONS:
        return {"ok": False, "error": "unknown collection"}
    ensure_collection(collection)
    qdrant(
        "POST",
        f"/collections/{collection}/points/delete?wait=true",
        {"filter": qdrant_match_filter(filters)},
    )
    return {"ok": True, "collection": collection, "filters": filters}


def delete_source_payload(payload: dict[str, Any]) -> dict[str, Any]:
    collection = normalize_collection(payload.get("collection"))
    source_path = payload.get("source_path")
    if not collection or collection not in COLLECTIONS:
        return {"ok": False, "error": "known collection is required"}
    if not source_path:
        return {"ok": False, "error": "source_path is required"}
    return delete_points_by_filter(collection, {"source_path": source_path})


def delete_stale_sources_payload(payload: dict[str, Any]) -> dict[str, Any]:
    collection = normalize_collection(payload.get("collection"))
    if not collection or collection not in COLLECTIONS:
        return {"ok": False, "error": "known collection is required"}
    active = set(str(item) for item in (payload.get("active_source_paths") or []))
    source_type = payload.get("source_type")
    project = payload.get("project")
    ensure_collection(collection)

    offset = None
    stale_paths: set[str] = set()
    while True:
        scroll_payload: dict[str, Any] = {
            "limit": 100,
            "with_payload": True,
            "with_vector": False,
        }
        if offset is not None:
            scroll_payload["offset"] = offset
        filters = qdrant_match_filter({"source_type": source_type, "project": project})
        if filters:
            scroll_payload["filter"] = filters
        result = qdrant("POST", f"/collections/{collection}/points/scroll", scroll_payload)
        body = result.get("result") or {}
        for point in body.get("points") or []:
            source_path = ((point.get("payload") or {}).get("source_path") or "").strip()
            if source_path and source_path not in active:
                stale_paths.add(source_path)
        offset = body.get("next_page_offset")
        if not offset:
            break

    deleted = []
    for source_path in sorted(stale_paths):
        delete_points_by_filter(collection, {"source_path": source_path})
        deleted.append(source_path)
    return {"ok": True, "collection": collection, "deleted": deleted, "deleted_count": len(deleted)}


def upsert_text_document(collection: str, text: str, metadata: dict[str, Any]) -> dict[str, Any]:
    collection = normalize_collection(collection) or ""
    if collection not in COLLECTIONS:
        return {"ok": False, "error": "unknown collection"}
    if not text.strip():
        return {"ok": False, "error": "text is required"}

    ensure_collection(collection)
    chunks = chunk_text(text)
    if not chunks:
        return {"ok": False, "error": "no text chunks extracted"}

    content_sha256 = metadata.get("content_sha256") or document_hash(text)
    if metadata.get("dedupe", True):
        existing = find_existing_document_by_hash(collection, content_sha256)
        if existing:
            return {
                "ok": True,
                "duplicate": True,
                "collection": collection,
                "chunks": 0,
                "source_path": existing.get("source_path"),
                "existing": existing,
                "content_sha256": content_sha256,
            }

    source_path = metadata.get("source_path") or f"mattermost/{collection}/{uuid.uuid4()}.txt"
    created_at = metadata.get("created_at") or now_iso()
    base = {
        "source_type": metadata.get("source_type") or "mattermost_text",
        "collection": collection,
        "title": metadata.get("title") or "Mattermost knowledge",
        "author": metadata.get("author") or metadata.get("user_name") or "mattermost",
        "project": metadata.get("project") or "AIGILE",
        "tags": metadata.get("tags") if isinstance(metadata.get("tags"), list) else ["mattermost"],
        "created_at": created_at,
        "updated_at": metadata.get("updated_at") or created_at,
        "version": metadata.get("version") or "1",
        "language": metadata.get("language") or "ru",
        "access_level": metadata.get("access_level") or "local",
        "source_path": source_path,
        "mattermost_channel_id": metadata.get("mattermost_channel_id"),
        "mattermost_channel_name": metadata.get("mattermost_channel_name"),
        "mattermost_post_id": metadata.get("mattermost_post_id"),
        "mattermost_file_id": metadata.get("mattermost_file_id"),
        "content_sha256": content_sha256,
    }

    points = []
    for index, chunk in enumerate(chunks):
        payload = {
            **base,
            "chunk_index": index,
            "chunk_count": len(chunks),
            "text": chunk,
            "ingested_at": now_iso(),
        }
        points.append(
            {
                "id": point_id(collection, source_path, index, chunk),
                "vector": embed(chunk),
                "payload": payload,
            }
        )

    qdrant("PUT", f"/collections/{collection}/points?wait=true", {"points": points})
    return {"ok": True, "collection": collection, "chunks": len(points), "source_path": source_path}


def ingest_collection(collection: str) -> dict[str, Any]:
    ensure_collection(collection)
    folder = DATA_ROOT / collection
    folder.mkdir(parents=True, exist_ok=True)

    files = [
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".txt", ".json"}
    ]
    points: list[dict[str, Any]] = []
    indexed_files = 0

    for path in files:
        text, override = read_document(path)
        chunks = chunk_text(text)
        if not chunks:
            continue
        indexed_files += 1
        metadata = metadata_for(path, collection, override)
        for index, chunk in enumerate(chunks):
            payload = {
                **metadata,
                "chunk_index": index,
                "chunk_count": len(chunks),
                "text": chunk,
                "ingested_at": now_iso(),
            }
            points.append(
                {
                    "id": point_id(collection, metadata["source_path"], index, chunk),
                    "vector": embed(chunk),
                    "payload": payload,
                }
            )

    if points:
        qdrant("PUT", f"/collections/{collection}/points?wait=true", {"points": points})

    return {"collection": collection, "files": indexed_files, "chunks": len(points)}


def ingest(payload: dict[str, Any]) -> dict[str, Any]:
    requested = payload.get("collections") or COLLECTIONS
    collections = [item for item in requested if item in COLLECTIONS]
    started = time.time()
    results = [ingest_collection(collection) for collection in collections]
    return {
        "ok": True,
        "collections": results,
        "duration_seconds": round(time.time() - started, 3),
        "embedding_provider": EMBEDDING_PROVIDER,
        "embedding_model": EMBEDDING_MODEL if EMBEDDING_PROVIDER == "ollama" else "local-hash",
    }


def query_collection(collection: str, query: str, limit: int) -> list[dict[str, Any]]:
    ensure_collection(collection)
    result = qdrant(
        "POST",
        f"/collections/{collection}/points/search",
        {
            "vector": embed(query),
            "limit": limit,
            "with_payload": True,
        },
    )
    matches = result.get("result") or []
    normalized = []
    for match in matches:
        payload = match.get("payload") or {}
        normalized.append(
            {
                "collection": collection,
                "score": match.get("score"),
                "title": payload.get("title"),
                "source_path": payload.get("source_path"),
                "metadata": {key: payload.get(key) for key in [
                    "source_type",
                    "collection",
                    "title",
                    "author",
                    "project",
                    "tags",
                    "created_at",
                    "updated_at",
                    "version",
                    "language",
                    "access_level",
                    "source_path",
                ]},
                "text": payload.get("text", ""),
            }
        )
    return normalized


def rag_query(payload: dict[str, Any]) -> dict[str, Any]:
    query = (payload.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query is required"}
    limit = int(payload.get("limit") or TOP_K)
    search_limit = max(limit, int(payload.get("search_limit") or 30))
    requested = payload.get("collections") or ([payload.get("collection")] if payload.get("collection") else COLLECTIONS)
    collections = [normalize_collection(item) for item in requested]
    collections = [item for item in collections if item in COLLECTIONS]
    matches: list[dict[str, Any]] = []
    for collection in collections:
        matches.extend(query_collection(collection, query, search_limit))
    for match in matches:
        lexical = lexical_score(query, match.get("text") or "", match.get("title"))
        match["lexical_score"] = round(lexical, 3)
        match["rank_score"] = round((match.get("score") or 0) + lexical, 6)
    matches.sort(key=lambda item: item.get("rank_score") or item.get("score") or 0, reverse=True)
    return {"ok": True, "query": query, "matches": matches[:limit], "collections": collections}


def parse_kb_query(raw_query: str) -> dict[str, Any]:
    tokens = raw_query.strip().split()
    verbosity = "normal"
    reset = False
    cleaned: list[str] = []
    for token in tokens:
        lower = token.lower()
        if lower in {"--short", "-s", "short:", "кратко:"}:
            verbosity = "short"
        elif lower in {"--deep", "-d", "deep:", "подробно:", "глубоко:"}:
            verbosity = "deep"
        elif lower in {"--normal", "normal:"}:
            verbosity = "normal"
        elif lower in {"--reset", "reset", "сброс", "сбросить"}:
            reset = True
        else:
            cleaned.append(token)
    return {"query": " ".join(cleaned).strip(), "verbosity": verbosity, "reset": reset}


def conversation_key(payload: dict[str, Any], collection: str) -> str | None:
    user_id = (payload.get("user_id") or payload.get("user_name") or "").strip()
    channel_id = (payload.get("channel_id") or "").strip()
    if not user_id or not channel_id:
        return None
    return f"{collection}:{channel_id}:{user_id}"


def load_conversations() -> dict[str, Any]:
    if not CONVERSATION_STATE_PATH.exists():
        return {}
    try:
        return json.loads(CONVERSATION_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_conversations(state: dict[str, Any]) -> None:
    CONVERSATION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONVERSATION_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_conversation_history(key: str | None) -> list[dict[str, str]]:
    if not key:
        return []
    state = load_conversations()
    history = state.get(key) or []
    return history if isinstance(history, list) else []


def append_conversation_turn(key: str | None, question: str, answer: str) -> None:
    if not key:
        return
    state = load_conversations()
    history = state.get(key) if isinstance(state.get(key), list) else []
    history.append({"question": question, "answer": answer[:2000], "at": now_iso()})
    state[key] = history[-CONVERSATION_MAX_TURNS:]
    save_conversations(state)


def reset_conversation(key: str | None) -> None:
    if not key:
        return
    state = load_conversations()
    state.pop(key, None)
    save_conversations(state)


def format_history(history: list[dict[str, str]]) -> str:
    if not history:
        return "No previous conversation."
    lines = []
    for item in history[-CONVERSATION_MAX_TURNS:]:
        lines.append(f"User: {item.get('question', '')}")
        lines.append(f"Assistant: {item.get('answer', '')[:1200]}")
    return "\n".join(lines)


def verbosity_instruction(verbosity: str) -> str:
    if verbosity == "short":
        return "Answer very briefly: 3-5 short bullet points maximum."
    if verbosity == "deep":
        return "Answer in depth with sections: Summary, Details, Practical implications, Risks or caveats, Next questions."
    return "Answer concisely but use enough detail to be useful."


def answer_query(payload: dict[str, Any]) -> dict[str, Any]:
    collection = normalize_collection(payload.get("collection"))
    parsed = parse_kb_query((payload.get("query") or "").strip())
    query = parsed["query"]
    verbosity = payload.get("verbosity") or parsed["verbosity"]
    key = conversation_key(payload, collection or "")

    if not collection or collection not in COLLECTIONS:
        return {"ok": False, "error": "known collection is required"}

    if parsed["reset"]:
        reset_conversation(key)
        return {
            "ok": True,
            "collection": collection,
            "query": query,
            "answer": "История диалога сброшена.",
            "matches": [],
            "mattermost_payload": {
                "response_type": "ephemeral",
                "text": "История диалога сброшена. Можно начинать новый вопрос через `/kb`.",
            },
        }

    if not query:
        return {"ok": False, "error": "query is required"}

    history = get_conversation_history(key)
    history_text = format_history(history)
    search_query = query
    if history:
        recent_questions = " ".join(item.get("question", "") for item in history[-3:])
        search_query = f"{recent_questions} {query}".strip()

    search = rag_query({"collection": collection, "query": search_query, "limit": int(payload.get("limit") or TOP_K), "search_limit": 30})
    matches = search.get("matches", [])
    min_score = float(payload.get("min_score") or 0.01)
    relevant = [match for match in matches if (match.get("score") or 0) >= min_score or (match.get("lexical_score") or 0) > 0]
    if not relevant:
        answer = "Недостаточно информации в локальной базе знаний."
        append_conversation_turn(key, query, answer)
        return {
            "ok": True,
            "collection": collection,
            "query": query,
            "answer": answer,
            "matches": matches,
            "mattermost_payload": {"response_type": "ephemeral", "text": answer},
        }

    context = build_context(relevant)
    prompt = f"""
You are AIGILE Knowledge Agent.
Answer in Russian using only the local RAG context below.
Use the conversation history only to understand follow-up questions; do not invent facts from it.
If the RAG context is insufficient, say that there is not enough information.
{verbosity_instruction(verbosity)}

Collection: {collection}
Verbosity: {verbosity}

Conversation history:
{history_text}

Current question:
{query}

Local RAG context:
{context}
""".strip()
    result = http_json(
        "POST",
        f"{OLLAMA_BASE_URL}/api/chat",
        {
            "model": OLLAMA_MODEL,
            "stream": False,
            "messages": [
                {"role": "system", "content": "You answer only from local RAG context. No cloud APIs."},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=180,
    )
    answer = result.get("message", {}).get("content") or result.get("response") or "Ответ сформирован, но модель вернула пустой текст."
    append_conversation_turn(key, query, answer)
    docs = "\n".join(
        f"- `{match.get('source_path')}` score={round(match.get('score') or 0, 3)} lexical={match.get('lexical_score') or 0}"
        for match in relevant[:5]
    )
    history_note = "\n_Контекст диалога: включен. `/kb --reset` сбросит историю._" if key else ""
    text = f"""{answer}

---
**Использованный контекст**
{docs}{history_note}"""
    return {
        "ok": True,
        "collection": collection,
        "query": query,
        "verbosity": verbosity,
        "conversation_key": key,
        "history_turns": len(history) + 1 if key else 0,
        "answer": answer,
        "matches": relevant,
        "mattermost_payload": {"response_type": "ephemeral", "text": text},
    }


def decode_file_text(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    filename = payload.get("filename") or "mattermost-file.txt"
    content_type = payload.get("content_type") or ""
    if payload.get("text"):
        return str(payload["text"]), {"source_type": "mattermost_file", "title": filename}
    if payload.get("content_base64"):
        raw = base64.b64decode(payload["content_base64"])
        text, source_type = extract_text_from_file_bytes(filename, content_type, raw)
        if text:
            return text, {"source_type": source_type, "title": filename}
        return "", {"source_type": "mattermost_file_unsupported", "title": filename}
    return "", {"source_type": "mattermost_file_empty", "title": filename}


def extract_text_from_file_bytes(filename: str, content_type: str, raw: bytes) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"} or content_type.startswith("text/"):
        return raw.decode("utf-8", errors="replace"), f"mattermost_file:{suffix.lstrip('.') or 'txt'}"
    if suffix == ".json" or content_type == "application/json":
        return raw.decode("utf-8", errors="replace"), "mattermost_file:json"
    if suffix == ".pdf" or content_type == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(page.strip() for page in pages if page and page.strip()).strip()
            if not text:
                raise RuntimeError("PDF has no extractable text. Scanned PDFs need OCR, which is not enabled yet.")
            return text, "mattermost_file:pdf"
        except Exception as exc:
            raise RuntimeError(f"PDF text extraction failed for {filename}: {exc}") from exc
    return "", "mattermost_file_unsupported"


def ingest_text_payload(payload: dict[str, Any]) -> dict[str, Any]:
    collection = normalize_collection(payload.get("collection"))
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    metadata.update({
        "source_type": metadata.get("source_type") or "mattermost_text",
        "title": metadata.get("title") or payload.get("title") or "Mattermost message",
        "source_path": metadata.get("source_path") or payload.get("source_path") or f"mattermost/{collection}/{payload.get('post_id') or uuid.uuid4()}.txt",
    })
    return upsert_text_document(collection or "", str(payload.get("text") or ""), metadata)


def ingest_file_payload(payload: dict[str, Any]) -> dict[str, Any]:
    collection = normalize_collection(payload.get("collection"))
    text, file_metadata = decode_file_text(payload)
    if not text:
        return {"ok": False, "error": "unsupported or empty file"}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    metadata.update(file_metadata)
    metadata["source_path"] = metadata.get("source_path") or f"mattermost/{collection}/{payload.get('file_id') or uuid.uuid4()}-{file_metadata.get('title')}"
    metadata["mattermost_file_id"] = payload.get("file_id")
    return upsert_text_document(collection or "", text, metadata)


def mattermost_headers() -> dict[str, str]:
    if not MATTERMOST_TOKEN:
        raise RuntimeError("MATTERMOST_BOT_TOKEN is not configured")
    return {"Authorization": f"Bearer {MATTERMOST_TOKEN}"}


def mattermost_json(path: str) -> dict[str, Any]:
    return http_json("GET", f"{MATTERMOST_URL}{path}", None, timeout=60 | 0)


def mattermost_get_json(path: str) -> dict[str, Any]:
    req = request.Request(f"{MATTERMOST_URL}{path}", method="GET", headers=mattermost_headers())
    try:
        with request.urlopen(req, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Mattermost HTTP {exc.code}: {detail[:500]}") from exc


def mattermost_get_file(file_id: str) -> tuple[bytes, dict[str, str]]:
    return http_bytes("GET", f"{MATTERMOST_URL}/api/v4/files/{file_id}", mattermost_headers(), timeout=120)


def mattermost_create_post(channel_id: str, message: str, root_id: str | None = None) -> dict[str, Any]:
    body = {
        "channel_id": channel_id,
        "message": message,
    }
    if root_id:
        body["root_id"] = root_id
    return http_json("POST", f"{MATTERMOST_URL}/api/v4/posts", body, timeout=60, headers=mattermost_headers())


def safe_mattermost_reply(channel_id: str, post_id: str, message: str) -> None:
    try:
        mattermost_create_post(channel_id, message, root_id=post_id)
    except Exception as exc:
        print(f"Mattermost reply failed for post {post_id}: {exc}", flush=True)


def format_ingest_reply(collection: str, file_results: list[dict[str, Any]]) -> str:
    successful = [item for item in file_results if item.get("ok")]
    duplicates = [item for item in successful if item.get("duplicate")]
    new_documents = [item for item in successful if not item.get("duplicate")]
    failed = [item for item in file_results if not item.get("ok")]
    if new_documents and not failed and not duplicates:
        lines = ["**\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442 \u0437\u0430\u0433\u0440\u0443\u0436\u0435\u043d \u0432 \u0431\u0430\u0437\u0443 \u0437\u043d\u0430\u043d\u0438\u0439.**"]
        for item in new_documents:
            filename = item.get("filename") or item.get("title") or "document"
            chunks = item.get("chunks", 0)
            lines.append(f"- `{filename}` -> `{collection}`, chunks: {chunks}")
        return "\n".join(lines)

    if duplicates and not new_documents and not failed:
        lines = ["**\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442 \u0443\u0436\u0435 \u0435\u0441\u0442\u044c \u0432 \u0431\u0430\u0437\u0435 \u0437\u043d\u0430\u043d\u0438\u0439.**"]
        for item in duplicates:
            filename = item.get("filename") or item.get("title") or "document"
            existing = item.get("existing") if isinstance(item.get("existing"), dict) else {}
            source_path = existing.get("source_path") or item.get("source_path") or collection
            lines.append(f"- `{filename}` \u043d\u0435 \u0437\u0430\u0433\u0440\u0443\u0436\u0435\u043d \u043f\u043e\u0432\u0442\u043e\u0440\u043d\u043e; \u0443\u0436\u0435 \u0435\u0441\u0442\u044c: `{source_path}`")
        return "\n".join(lines)

    if successful and failed:
        lines = ["**\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442 \u0447\u0430\u0441\u0442\u0438\u0447\u043d\u043e \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d.**"]
        for item in new_documents:
            filename = item.get("filename") or item.get("title") or "document"
            lines.append(f"- OK: `{filename}` -> `{collection}`, chunks: {item.get('chunks', 0)}")
        for item in duplicates:
            filename = item.get("filename") or item.get("title") or "document"
            lines.append(f"- Duplicate: `{filename}` \u0443\u0436\u0435 \u0435\u0441\u0442\u044c \u0432 \u0431\u0430\u0437\u0435")
        for item in failed:
            filename = item.get("filename") or "document"
            lines.append(f"- \u041e\u0448\u0438\u0431\u043a\u0430: `{filename}` \u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u0442\u044c.")
        return "\n".join(lines)

    if new_documents or duplicates:
        lines = ["**\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d.**"]
        for item in new_documents:
            filename = item.get("filename") or item.get("title") or "document"
            lines.append(f"- OK: `{filename}` -> `{collection}`, chunks: {item.get('chunks', 0)}")
        for item in duplicates:
            filename = item.get("filename") or item.get("title") or "document"
            lines.append(f"- Duplicate: `{filename}` \u0443\u0436\u0435 \u0435\u0441\u0442\u044c \u0432 \u0431\u0430\u0437\u0435")
        return "\n".join(lines)

    return "**\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442 \u043d\u0435 \u0437\u0430\u0433\u0440\u0443\u0436\u0435\u043d.** \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0438\u0437\u0432\u043b\u0435\u0447\u044c \u0442\u0435\u043a\u0441\u0442. \u0415\u0441\u043b\u0438 \u044d\u0442\u043e \u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0439 PDF, \u043d\u0443\u0436\u0435\u043d OCR."


def load_poll_state() -> dict[str, Any]:
    if not POLL_STATE_PATH.exists():
        return {}
    try:
        return json.loads(POLL_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_poll_state(state: dict[str, Any]) -> None:
    POLL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    POLL_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def mattermost_channel_config() -> dict[str, Any]:
    try:
        config = json.loads(MATTERMOST_RAG_CHANNELS_JSON)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MATTERMOST_RAG_CHANNELS_JSON is invalid") from exc
    return config.get("channels") or config


def ingest_mattermost_post(channel_id: str, mapping: dict[str, Any], post: dict[str, Any], processed_files: set[str] | None = None) -> dict[str, Any]:
    collection = normalize_collection(mapping.get("collection"))
    if collection not in COLLECTIONS:
        return {"ok": False, "error": "unknown mapped collection", "post_id": post.get("id")}

    post_id = post.get("id") or ""
    if post.get("root_id"):
        return {"ok": True, "ignored": True, "reason": "thread reply", "post_id": post_id}
    if (post.get("props") or {}).get("from_webhook") or (post.get("props") or {}).get("override_username"):
        return {"ok": True, "ignored": True, "reason": "bot post", "post_id": post_id}
    message = (post.get("message") or "").strip()
    user_id = post.get("user_id") or "mattermost"
    created_at = datetime.fromtimestamp((post.get("create_at") or int(time.time() * 1000)) / 1000, timezone.utc).isoformat()
    base_metadata = {
        "author": user_id,
        "source_type": "mattermost_post",
        "title": f"Mattermost post {post_id}",
        "created_at": created_at,
        "updated_at": now_iso(),
        "mattermost_channel_id": channel_id,
        "mattermost_channel_name": mapping.get("channel_name"),
        "mattermost_post_id": post_id,
    }

    results: list[dict[str, Any]] = []
    if message:
        results.append(
            upsert_text_document(
                collection,
                message,
                {
                    **base_metadata,
                    "source_path": f"mattermost/{collection}/{post_id}.txt",
                },
            )
        )

    files = ((post.get("metadata") or {}).get("files") or [])
    file_results: list[dict[str, Any]] = []
    for file_info in files:
        file_id = file_info.get("id")
        if not file_id:
            continue
        filename = file_info.get("name") or file_info.get("filename") or f"{file_id}.txt"
        content_type = file_info.get("mime_type") or file_info.get("mime") or ""
        try:
            raw, headers = mattermost_get_file(file_id)
            content_type = content_type or headers.get("Content-Type", "")
            text, source_type = extract_text_from_file_bytes(filename, content_type, raw)
            if not text:
                failure = {"ok": False, "error": "unsupported or empty file", "file_id": file_id, "filename": filename}
                results.append(failure)
                file_results.append(failure)
                continue
            ingest_result = upsert_text_document(
                collection,
                text,
                {
                    **base_metadata,
                    "source_type": source_type,
                    "title": filename,
                    "source_path": f"mattermost/{collection}/{file_id}-{filename}",
                    "mattermost_file_id": file_id,
                },
            )
            ingest_result["filename"] = filename
            ingest_result["file_id"] = file_id
            results.append(ingest_result)
            file_results.append(ingest_result)
        except Exception as exc:
            failure = {"ok": False, "error": str(exc), "file_id": file_id, "filename": filename}
            results.append(failure)
            file_results.append(failure)

    new_file_results = [item for item in file_results if item.get("file_id") and item.get("file_id") not in (processed_files or set())]
    if new_file_results:
        safe_mattermost_reply(channel_id, post_id, format_ingest_reply(collection, new_file_results))

    return {"ok": True, "post_id": post_id, "results": results}


def poll_mattermost_rag(payload: dict[str, Any]) -> dict[str, Any]:
    if not MATTERMOST_RAG_INGEST_ENABLED and not payload.get("force"):
        return {"ok": True, "ignored": True, "message": "Mattermost RAG ingest is disabled."}

    channels = mattermost_channel_config()
    if not channels:
        return {"ok": True, "ignored": True, "message": "No Mattermost RAG channels configured."}

    state = load_poll_state()
    poll_started_ms = int(time.time() * 1000)
    results = []
    for channel_id, mapping in channels.items():
        channel_state = state.get(channel_id)
        if not isinstance(channel_state, dict):
            channel_state = {"since": channel_state or 0, "processed_posts": [], "processed_files": []}
        processed_posts = set(channel_state.get("processed_posts") or [])
        processed_files = set(channel_state.get("processed_files") or [])
        requested_since = payload.get("since_millis")
        since = int(requested_since if requested_since is not None else (channel_state.get("since") or max(0, poll_started_ms - 300000)))
        posts_response = mattermost_get_json(f"/api/v4/channels/{channel_id}/posts?since={since}")
        order = list(reversed(posts_response.get("order") or []))
        channel_results = []
        max_seen = since
        for post_id in order:
            post = (posts_response.get("posts") or {}).get(post_id) or {}
            if post.get("type"):
                continue
            max_seen = max(max_seen, int(post.get("create_at") or since))
            files = ((post.get("metadata") or {}).get("files") or [])
            file_ids = [item.get("id") for item in files if item.get("id")]
            if post_id in processed_posts or (file_ids and all(file_id in processed_files for file_id in file_ids)):
                channel_results.append({"ok": True, "ignored": True, "reason": "already processed", "post_id": post_id})
                continue
            result = ingest_mattermost_post(channel_id, mapping, post, processed_files)
            channel_results.append(result)
            processed_posts.add(post_id)
            for file_id in file_ids:
                processed_files.add(file_id)
        state[channel_id] = {
            "since": max(max_seen + 1, poll_started_ms),
            "processed_posts": list(processed_posts)[-1000:],
            "processed_files": list(processed_files)[-1000:],
        }
        results.append({"channel_id": channel_id, "collection": mapping.get("collection"), "posts": len(channel_results), "results": channel_results})
    save_poll_state(state)
    return {"ok": True, "channels": results}


def extract_issue(payload: dict[str, Any]) -> dict[str, Any]:
    body = payload.get("body") if isinstance(payload.get("body"), dict) else payload
    issue = body.get("issue") or body.get("data") or body
    nested = issue.get("issue") if isinstance(issue.get("issue"), dict) else {}
    return {
        "title": issue.get("name") or issue.get("title") or nested.get("name") or "Untitled Plane item",
        "description": issue.get("description_stripped") or issue.get("description") or issue.get("description_html") or nested.get("description") or "",
        "priority": issue.get("priority") or nested.get("priority") or "unknown",
        "state": (issue.get("state_detail") or {}).get("name") if isinstance(issue.get("state_detail"), dict) else issue.get("state") or nested.get("state") or "unknown",
        "project": (issue.get("project_detail") or {}).get("name") if isinstance(issue.get("project_detail"), dict) else issue.get("project") or body.get("project", {}).get("name") if isinstance(body.get("project"), dict) else "unknown",
        "url": issue.get("url") or issue.get("issue_url") or body.get("url") or "",
    }


def build_context(matches: list[dict[str, Any]]) -> str:
    parts = []
    for index, match in enumerate(matches, start=1):
        parts.append(
            f"[{index}] {match.get('collection')} / {match.get('title')} / {match.get('source_path')}\n"
            f"{(match.get('text') or '')[:1200]}"
        )
    return "\n\n---\n\n".join(parts)


def ask_ollama(issue: dict[str, Any], matches: list[dict[str, Any]]) -> str:
    context = build_context(matches)
    prompt = f"""
You are AI Delivery Assistant for the local self-hosted AIGILE delivery platform.
Use only the Plane issue and the provided local RAG context. Do not call cloud APIs.
Return concise Russian markdown.

Plane issue:
- Project: {issue['project']}
- Title: {issue['title']}
- State: {issue['state']}
- Priority: {issue['priority']}
- URL: {issue['url']}
- Description:
{issue['description']}

Local RAG context:
{context or 'No related context found.'}

Output sections:
1. Р С™РЎР‚Р В°РЎвЂљР С”Р С•
2. Risk level: Low/Medium/High
3. Blockers: yes or no + why
4. 2-3 РЎР‚Р ВµР С”Р С•Р СР ВµР Р…Р Т‘Р В°РЎвЂ Р С‘Р С‘
5. Р СџР С•Р В»Р Р…РЎвЂ№Р в„– Р В°Р Р…Р В°Р В»Р С‘Р В·
6. Acceptance criteria
7. Implementation suggestion
8. Codex-ready prompt
""".strip()
    result = http_json(
        "POST",
        f"{OLLAMA_BASE_URL}/api/chat",
        {
            "model": OLLAMA_MODEL,
            "stream": False,
            "messages": [
                {"role": "system", "content": "You are a local AI delivery assistant. Be concise, practical, and self-hosted-only."},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=180,
    )
    return result.get("message", {}).get("content") or result.get("response") or json.dumps(result, ensure_ascii=False)


def extract_preview(analysis: str) -> dict[str, Any]:
    lower = analysis.lower()
    risk = "Medium"
    if "risk level: high" in lower or "РЎР‚Р С‘РЎРѓР С”: high" in lower or "Р Р†РЎвЂ№РЎРѓР С•Р С”" in lower:
        risk = "High"
    elif "risk level: low" in lower or "РЎР‚Р С‘РЎРѓР С”: low" in lower or "Р Р…Р С‘Р В·Р С”" in lower:
        risk = "Low"
    lines = [line.strip("-* ").strip() for line in analysis.splitlines() if line.strip()]
    blocker_line = next((line.lower() for line in lines if "blocker" in line.lower() or "Р В±Р В»Р С•Р С”Р ВµРЎР‚" in line.lower()), "")
    if re.search(r"\b(no|Р Р…Р ВµРЎвЂљ)\b", blocker_line):
        blockers = "no"
    elif re.search(r"\b(yes|Р Т‘Р В°)\b", blocker_line):
        blockers = "yes"
    else:
        blockers = "yes" if any(marker in lower for marker in ["blocked", "Р В·Р В°Р Р†Р С‘РЎРѓР С‘Р С"]) else "no"
    summary = next((line for line in lines if len(line) > 25), lines[0] if lines else "AI analysis completed.")
    recommendations = [line for line in lines if len(line) > 20][:3]
    return {"summary": summary[:280], "risk_level": risk, "blockers": blockers, "recommendations": recommendations}


def mattermost_text(issue: dict[str, Any], analysis: str, matches: list[dict[str, Any]]) -> str:
    preview = extract_preview(analysis)
    related_docs = "\n".join(
        f"- `{match.get('collection')}` / {match.get('title')} / {match.get('source_path')}"
        for match in matches[:5]
    ) or "- no related docs found"
    recommendations = "\n".join(f"- {item}" for item in preview["recommendations"]) or "- Р Р€РЎвЂљР С•РЎвЂЎР Р…Р С‘РЎвЂљРЎРЉ Р С—Р С•РЎРѓРЎвЂљР В°Р Р…Р С•Р Р†Р С”РЎС“ Р В·Р В°Р Т‘Р В°РЎвЂЎР С‘."
    return f"""**AI Р В°Р Р…Р В°Р В»Р С‘Р В· Р В·Р В°Р Т‘Р В°РЎвЂЎР С‘ Р С–Р С•РЎвЂљР С•Р Р†:** `{issue['title']}`

Р С™РЎР‚Р В°РЎвЂљР С”Р С•: {preview['summary']}

Risk level: **{preview['risk_level']}**
Blockers: **{preview['blockers']}**

Р В Р ВµР С”Р С•Р СР ВµР Р…Р Т‘Р В°РЎвЂ Р С‘Р С‘:
{recommendations}

<details>
<summary>Р СџР С•Р С”Р В°Р В·Р В°РЎвЂљРЎРЉ Р С—Р С•Р В»Р Р…РЎвЂ№Р в„– AI-Р В°Р Р…Р В°Р В»Р С‘Р В·</summary>

## Context used
{related_docs}

## Full analysis
{analysis}

</details>"""


def analyze_issue(payload: dict[str, Any]) -> dict[str, Any]:
    issue = extract_issue(payload)
    query_text = "\n".join([issue["project"], issue["title"], issue["description"]]).strip()
    search = rag_query({"query": query_text, "limit": int(payload.get("limit") or TOP_K)})
    matches = search.get("matches", [])
    analysis = ask_ollama(issue, matches)
    preview = extract_preview(analysis)
    return {
        "ok": True,
        "issue": issue,
        "preview": preview,
        "analysis": analysis,
        "context_used": matches,
        "mattermost_payload": {
            "username": "AI Delivery Assistant",
            "text": mattermost_text(issue, analysis, matches),
        },
    }


class RagHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_OPTIONS(self) -> None:
        respond(self, 200, {"ok": True})

    def do_GET(self) -> None:
        if self.path == "/health":
            try:
                ensure_collections()
                qdrant_ok = True
            except Exception:
                qdrant_ok = False
            respond(self, 200, {"ok": True, "service": "aigile-rag-backend", "qdrant": qdrant_ok})
            return
        respond(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        try:
            payload = read_json(self)
            if self.path == "/collections/list":
                ensure_collections()
                respond(self, 200, {"ok": True, "collections": COLLECTIONS})
            elif self.path == "/rag/ingest":
                respond(self, 200, ingest(payload))
            elif self.path in {"/rag/query", "/context/search"}:
                result = rag_query(payload)
                respond(self, 200 if result.get("ok") else 400, result)
            elif self.path == "/rag/answer":
                result = answer_query(payload)
                respond(self, 200 if result.get("ok") else 400, result)
            elif self.path == "/rag/ingest-text":
                result = ingest_text_payload(payload)
                respond(self, 200 if result.get("ok") else 400, result)
            elif self.path == "/rag/ingest-file":
                result = ingest_file_payload(payload)
                respond(self, 200 if result.get("ok") else 400, result)
            elif self.path == "/rag/delete-source":
                result = delete_source_payload(payload)
                respond(self, 200 if result.get("ok") else 400, result)
            elif self.path == "/rag/delete-stale-sources":
                result = delete_stale_sources_payload(payload)
                respond(self, 200 if result.get("ok") else 400, result)
            elif self.path == "/mattermost/poll-rag":
                result = poll_mattermost_rag(payload)
                respond(self, 200 if result.get("ok") else 400, result)
            elif self.path == "/rag/analyze-issue":
                respond(self, 200, analyze_issue(payload))
            else:
                respond(self, 404, {"ok": False, "error": "not found"})
        except Exception as exc:
            respond(self, 500, {"ok": False, "error": str(exc)})


def main() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    ensure_collections()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), RagHandler)
    print(f"AIGILE RAG backend listening on :{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
