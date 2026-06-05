import json
import logging
import os
import re
import hashlib
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.production")

import django  # noqa: E402

django.setup()

from django.db import close_old_connections, transaction  # noqa: E402
from django.utils.html import escape, strip_tags  # noqa: E402
from plane.db.models import CycleIssue, Issue, IssueComment, IssueLabel, IssueRelation, IssueSequence, IssueView, Label, ModuleIssue, Page, Project, ProjectPage, State, Workspace  # noqa: E402


PORT = int(os.environ.get("AIGILE_BACKEND_PORT", "8091"))
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b-instruct")
OLLAMA_FALLBACK_MODELS = [
    item.strip()
    for item in os.environ.get("AIGILE_OLLAMA_FALLBACK_MODELS", "qwen2.5-coder:7b-instruct").split(",")
    if item.strip()
]
RAG_BACKEND_URL = os.environ.get("RAG_BACKEND_URL", "http://rag-backend:8092").rstrip("/")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")
N8N_INTERNAL_URL = os.environ.get("N8N_INTERNAL_URL", "http://n8n:5678").rstrip("/")
PLANE_INTERNAL_URL = os.environ.get("PLANE_INTERNAL_URL", "http://plane-proxy").rstrip("/")
OPEN_WEBUI_INTERNAL_URL = os.environ.get("OPEN_WEBUI_INTERNAL_URL", "http://open-webui:8080").rstrip("/")
MATTERMOST_WEBHOOK_URL = os.environ["MATTERMOST_WEBHOOK_URL"]
MATTERMOST_INTERNAL_URL = os.environ.get("MATTERMOST_INTERNAL_URL", "http://mattermost:8065").rstrip("/")
MATTERMOST_PUBLIC_URL = os.environ.get("MATTERMOST_PUBLIC_URL", "http://localhost:8065").rstrip("/")
MATTERMOST_BOT_TOKEN = os.environ.get("MATTERMOST_BOT_TOKEN", "")
MATTERMOST_DEFAULT_USERNAME = os.environ.get("AIGILE_MATTERMOST_DEFAULT_USERNAME", "admin")
KB_PATH = Path(os.environ.get("AIGILE_KB_PATH", "/data/knowledge-base/latest.md"))
LOG_PATH = Path(os.environ.get("AIGILE_LOG_PATH", "/data/logs/manual-trigger.log"))
REVIEW_GATE_ENABLED = os.environ.get("AIGILE_AI_REVIEW_GATE_ENABLED", "false").lower() == "true"
REVIEW_HISTORY_PATH = Path(os.environ.get("AIGILE_REVIEW_HISTORY_PATH", "/data/logs/ai-review-history.jsonl"))
APPLY_HISTORY_PATH = Path(os.environ.get("AIGILE_APPLY_HISTORY_PATH", "/data/logs/ai-apply-history.jsonl"))
DELIVERY_SIGNALS_PATH = Path(os.environ.get("AIGILE_DELIVERY_SIGNALS_PATH", "/data/logs/delivery-signals.jsonl"))
TASK_CHAT_CONTEXT_PATH = Path(os.environ.get("AIGILE_TASK_CHAT_CONTEXT_PATH", "/data/logs/task-chat-context.jsonl"))
TASK_CHAT_THREAD_ENABLED = os.environ.get("AIGILE_TASK_CHAT_THREAD_ENABLED", "true").lower() == "true"
TASK_CHAT_POLL_SECONDS = int(os.environ.get("AIGILE_TASK_CHAT_POLL_SECONDS", "8"))
TASK_CHAT_STATE_PATH = Path(os.environ.get("AIGILE_TASK_CHAT_STATE_PATH", "/data/logs/task-chat-thread-state.json"))
TASK_CHAT_HISTORY_LIMIT = int(os.environ.get("AIGILE_TASK_CHAT_HISTORY_LIMIT", "10"))
PLANE_PAGES_WORKSPACE_SLUG = os.environ.get("AIGILE_PLANE_PAGES_WORKSPACE_SLUG", "aigile")
PLANE_PAGES_PROJECT_IDENTIFIER = os.environ.get("AIGILE_PLANE_PAGES_PROJECT_IDENTIFIER", "AIGILE")
PLANE_PAGES_COLLECTION = os.environ.get("AIGILE_PLANE_PAGES_COLLECTION", "plane_pages")
PLANE_PAGES_TITLE_MARKER = os.environ.get("AIGILE_PLANE_PAGES_TITLE_MARKER", "[AI]")
PLANE_PAGES_BOOTSTRAP_RULES = os.environ.get("AIGILE_PLANE_PAGES_BOOTSTRAP_RULES", "true").lower() == "true"
REFRESH_HOUR = int(os.environ.get("AIGILE_KB_REFRESH_HOUR", "6"))
REFRESH_MINUTE = int(os.environ.get("AIGILE_KB_REFRESH_MINUTE", "0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("aigile.manual-trigger")

IN_FLIGHT: set[str] = set()
IN_FLIGHT_LOCK = threading.Lock()

KNOWN_ISSUE_TYPES = {
    "bug": "Bug",
    "баг": "Bug",
    "ошибка": "Bug",
    "дефект": "Bug",
    "story": "Story",
    "user story": "Story",
    "история": "Story",
    "epic": "Epic",
    "эпик": "Epic",
    "tech debt": "Tech Debt",
    "tech-debt": "Tech Debt",
    "technical debt": "Tech Debt",
    "техдолг": "Tech Debt",
    "технический долг": "Tech Debt",
    "research": "Research",
    "исследование": "Research",
    "release": "Release",
    "релиз": "Release",
    "task": "Task",
    "задача": "Task",
}

AGENT_MAP = {
    "Bug": ["QA Engineer Agent", "Backend Developer Agent", "Frontend Developer Agent", "Tech Lead Agent"],
    "Story": ["Product Owner Agent", "System Analyst Agent", "QA Engineer Agent", "UX/UI Agent", "Architect Agent"],
    "Task": ["Delivery Manager Agent", "Tech Lead Agent", "QA Engineer Agent"],
    "Epic": ["Product Manager Agent", "Architect Agent", "Delivery Manager Agent", "Security Engineer Agent", "QA Lead Agent"],
    "Tech Debt": ["Tech Lead Agent", "Architect Agent", "DevOps Agent", "QA Engineer Agent"],
    "Research": ["Product Manager Agent", "Business Analyst Agent", "Architect Agent"],
    "Release": ["Release Manager Agent", "QA Lead", "DevOps Agent", "Security Engineer Agent"],
}

TYPE_LABEL_NAMES = ["Epic", "Story", "Bug", "Task", "Tech Debt", "Research", "Release"]
TYPE_LABEL_ERROR = "Выбери тип задачи через метку Epic, Story, Bug, Task, Tech Debt, Research или Release."
DEMO_LABEL_NAME = "AIGILE-DEMO"
DEMO_TITLE_PREFIX = "[DEMO]"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_dirs() -> None:
    KB_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def append_execution_log(event: dict) -> None:
    ensure_dirs()
    line = json.dumps({"ts": utc_now_iso(), **event}, ensure_ascii=False)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def append_review_history(review: dict) -> None:
    ensure_dirs()
    REVIEW_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REVIEW_HISTORY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(review, ensure_ascii=False) + "\n")


def append_apply_history(event: dict) -> None:
    ensure_dirs()
    APPLY_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with APPLY_HISTORY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def append_delivery_signal(signal: dict) -> None:
    ensure_dirs()
    DELIVERY_SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DELIVERY_SIGNALS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(signal, ensure_ascii=False) + "\n")


def append_task_chat_context(event: dict) -> None:
    ensure_dirs()
    TASK_CHAT_CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TASK_CHAT_CONTEXT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_task_chat_contexts(limit: int = 200) -> list[dict]:
    if not TASK_CHAT_CONTEXT_PATH.exists():
        return []
    contexts = []
    with TASK_CHAT_CONTEXT_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("ok") and event.get("post_id") and event.get("channel_id"):
                contexts.append(event)
    return contexts[-limit:]


def read_task_chat_state() -> dict:
    if not TASK_CHAT_STATE_PATH.exists():
        return {"threads": {}}
    try:
        return json.loads(TASK_CHAT_STATE_PATH.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {"threads": {}}


def write_task_chat_state(state: dict) -> None:
    ensure_dirs()
    TASK_CHAT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASK_CHAT_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def mark_task_chat_thread_started(root_id: str, context_id: str) -> None:
    if not root_id:
        return
    state = read_task_chat_state()
    threads = state.setdefault("threads", {})
    thread = threads.setdefault(root_id, {})
    processed = set(thread.get("processed_post_ids") or [])
    processed.add(root_id)
    thread["processed_post_ids"] = sorted(processed)
    thread["context_id"] = context_id
    thread["started_at"] = thread.get("started_at") or utc_now_iso()
    write_task_chat_state(state)


def read_review_history(issue_key: str | None = None, limit: int = 20) -> list[dict]:
    if not REVIEW_HISTORY_PATH.exists():
        return []
    reviews = []
    with REVIEW_HISTORY_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                review = json.loads(line)
            except json.JSONDecodeError:
                continue
            if issue_key and review.get("issue_key") != issue_key:
                continue
            reviews.append(review)
    return reviews[-limit:]


def read_apply_history(limit: int = 200) -> list[dict]:
    if not APPLY_HISTORY_PATH.exists():
        return []
    events = []
    with APPLY_HISTORY_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events[-limit:]


def read_delivery_signals(status: str | None = None, limit: int = 500) -> list[dict]:
    if not DELIVERY_SIGNALS_PATH.exists():
        return []
    signals = {}
    with DELIVERY_SIGNALS_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            signal_id = event.get("id")
            if not signal_id:
                continue
            if event.get("event") == "status_update":
                if signal_id in signals:
                    signals[signal_id]["status"] = event.get("status") or signals[signal_id].get("status")
                    signals[signal_id]["updated_at"] = event.get("updated_at") or utc_now_iso()
                    signals[signal_id]["updated_by"] = event.get("updated_by")
                continue
            signals[signal_id] = event
    values = list(signals.values())
    if status:
        values = [signal for signal in values if signal.get("status") == status]
    return values[-limit:]


def update_delivery_signal_status(signal_id: str, status: str, updated_by: str = "system") -> dict:
    status = str(status or "").lower().strip()
    if status not in {"open", "acknowledged", "resolved"}:
        raise ValueError("Invalid signal status")
    existing = {signal.get("id") for signal in read_delivery_signals(limit=5000)}
    if signal_id not in existing:
        raise ValueError("Delivery signal not found")
    event = {
        "event": "status_update",
        "id": signal_id,
        "status": status,
        "updated_by": updated_by,
        "updated_at": utc_now_iso(),
    }
    append_delivery_signal(event)
    return {"ok": True, "id": signal_id, "status": status}


def find_review_history_item(issue_key: str, review_id: str) -> dict | None:
    for review in reversed(read_review_history(issue_key, limit=1000)):
        if review.get("review_id") == review_id:
            return review
    return None


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler: BaseHTTPRequestHandler, status: int, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def read_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length == 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw or "{}")


def probe_http_service(service_id: str, name: str, url: str, timeout: float = 2.0, public_url: str | None = None) -> dict:
    started = time.monotonic()
    result = {
        "id": service_id,
        "name": name,
        "kind": "http",
        "url": public_url or url,
        "internal_url": url,
        "ok": False,
        "status": "down",
        "status_code": None,
        "latency_ms": None,
        "error": None,
    }
    try:
        request = Request(url, headers={"User-Agent": "AIGILE-health-check/0.1"})
        with urlopen(request, timeout=timeout) as response:
            status_code = getattr(response, "status", None) or response.getcode()
            response.read(512)
        result["status_code"] = status_code
        result["ok"] = 200 <= int(status_code) < 400
        result["status"] = "ok" if result["ok"] else "warn"
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        result["latency_ms"] = round((time.monotonic() - started) * 1000)
    return result


def probe_plane_database() -> dict:
    started = time.monotonic()
    result = {
        "id": "plane_database",
        "name": "Plane database",
        "kind": "django",
        "url": "postgresql://plane-db/plane",
        "ok": False,
        "status": "down",
        "latency_ms": None,
        "error": None,
        "details": {},
    }
    try:
        close_old_connections()
        result["details"] = {
            "projects": Project.objects.filter(deleted_at__isnull=True).count(),
            "issues": Issue.objects.filter(deleted_at__isnull=True).count(),
        }
        result["ok"] = True
        result["status"] = "ok"
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        result["latency_ms"] = round((time.monotonic() - started) * 1000)
    return result


def build_health_report() -> dict:
    self_service = {
        "id": "aigile_backend",
        "name": "AIGILE backend",
        "kind": "self",
        "url": "http://localhost:8091",
        "ok": True,
        "status": "ok",
        "latency_ms": 0,
        "details": {
            "review_gate_enabled": REVIEW_GATE_ENABLED,
            "task_chat_enabled": TASK_CHAT_THREAD_ENABLED,
            "ollama_model": OLLAMA_MODEL,
        },
    }
    checks = [
        ("plane_database", probe_plane_database),
        ("plane_web", lambda: probe_http_service("plane_web", "Plane web", f"{PLANE_INTERNAL_URL}/", public_url="http://localhost:8080")),
        ("mattermost", lambda: probe_http_service("mattermost", "Mattermost", f"{MATTERMOST_INTERNAL_URL}/api/v4/system/ping", public_url=MATTERMOST_PUBLIC_URL)),
        ("n8n", lambda: probe_http_service("n8n", "n8n", f"{N8N_INTERNAL_URL}/healthz", public_url="http://localhost:5678")),
        ("ollama", lambda: probe_http_service("ollama", "Ollama", f"{OLLAMA_BASE_URL}/api/tags", public_url="http://localhost:11434")),
        ("rag_backend", lambda: probe_http_service("rag_backend", "AIGILE RAG backend", f"{RAG_BACKEND_URL}/health", public_url="http://localhost:8092")),
        ("qdrant", lambda: probe_http_service("qdrant", "Qdrant", f"{QDRANT_URL}/collections", public_url="http://localhost:6333")),
        ("open_webui", lambda: probe_http_service("open_webui", "Open WebUI", f"{OPEN_WEBUI_INTERNAL_URL}/", public_url="http://localhost:3001")),
    ]
    by_id = {"aigile_backend": self_service}
    with ThreadPoolExecutor(max_workers=len(checks)) as executor:
        futures = {executor.submit(fn): service_id for service_id, fn in checks}
        for future in as_completed(futures):
            service_id = futures[future]
            try:
                by_id[service_id] = future.result()
            except Exception as exc:
                by_id[service_id] = {
                    "id": service_id,
                    "name": service_id.replace("_", " ").title(),
                    "kind": "internal",
                    "url": "",
                    "ok": False,
                    "status": "down",
                    "latency_ms": 0,
                    "error": str(exc),
                }
    order = ["aigile_backend"] + [service_id for service_id, _ in checks]
    services = [by_id[service_id] for service_id in order]
    ok_count = sum(1 for service in services if service.get("ok"))
    down_count = len(services) - ok_count
    return {
        "ok": down_count == 0,
        "status": "ok" if down_count == 0 else "degraded",
        "created_at": utc_now_iso(),
        "services_total": len(services),
        "services_ok": ok_count,
        "services_down": down_count,
        "services": services,
    }


def issue_key(issue: Issue) -> str:
    return f"{issue.project.identifier}-{issue.sequence_id}"


def issue_url(issue: Issue) -> str:
    return f"http://localhost:8080/{issue.workspace.slug}/browse/{issue_key(issue)}"


def latest_reviews_by_issue(limit: int = 2000) -> dict[str, dict]:
    latest = {}
    for review in read_review_history(limit=limit):
        key = review.get("issue_key")
        if key:
            latest[key] = review
    return latest


def issue_has_acceptance_criteria(issue: Issue) -> bool:
    text = (issue.description_stripped or strip_tags(issue.description_html or "") or "").lower()
    return any(marker in text for marker in ["acceptance criteria", "acceptance criterion", "критерии приемки", "критерии приёмки"])


def issue_module_names(issue: Issue) -> list[str]:
    links = ModuleIssue.objects.select_related("module").filter(issue=issue, deleted_at__isnull=True)
    return [link.module.name for link in links if getattr(link, "module", None)]


def issue_cycle_names(issue: Issue) -> list[str]:
    links = CycleIssue.objects.select_related("cycle").filter(issue=issue, deleted_at__isnull=True)
    return [link.cycle.name for link in links if getattr(link, "cycle", None)]


def review_findings(review: dict) -> list[dict]:
    findings = []
    for agent in review.get("agents") or []:
        for finding in agent.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            findings.append({
                "agent": agent.get("agent_name") or "AI Agent",
                "status": agent.get("status") or review.get("overall_status") or "unknown",
                "severity": finding.get("severity") or "medium",
                "title": finding.get("title") or "AI review finding",
                "description": finding.get("description") or "",
                "recommendation": finding.get("recommendation") or "",
                "can_be_applied": bool(finding.get("can_be_applied")),
            })
    return findings


def build_delivery_intelligence_report() -> dict:
    close_old_connections()
    project = find_demo_project()
    reviews = latest_reviews_by_issue()
    apply_events = read_apply_history(limit=500)
    delivery_signals = read_delivery_signals(limit=1000)
    open_signals = [signal for signal in delivery_signals if signal.get("status", "open") == "open"]
    issues = list(
        Issue.objects.select_related("workspace", "project", "state", "type")
        .prefetch_related("labels")
        .filter(workspace=project.workspace, project=project, deleted_at__isnull=True, archived_at__isnull=True)
        .order_by("-updated_at")[:250]
    )
    reviewed = []
    unreviewed = []
    status_counts = {"green": 0, "yellow": 0, "red": 0, "unknown": 0}
    top_risks = []
    blockers = []
    requirement_quality = {
        "without_acceptance_criteria": [],
        "without_type_label": [],
        "missing_info": [],
        "yellow_red_qa_review": [],
        "risks_or_dependencies_detected": [],
    }
    module_signals: dict[str, dict] = {}
    decisions_needed = []
    open_questions = []
    action_items = []

    for issue in issues:
        key = issue_key(issue)
        labels = list(issue.labels.filter(deleted_at__isnull=True).values_list("name", flat=True))
        review = reviews.get(key)
        item = {
            "key": key,
            "title": issue.name,
            "url": issue_url(issue),
            "state": issue.state.name if issue.state else "",
            "state_group": issue.state.group if issue.state else "",
            "priority": issue.priority,
            "labels": labels,
            "modules": issue_module_names(issue),
            "cycles": issue_cycle_names(issue),
            "review": review,
        }
        if review:
            reviewed.append(item)
            status = review.get("overall_status") or "unknown"
            status_counts[status if status in status_counts else "unknown"] += 1
        else:
            unreviewed.append(item)

        if not issue_has_acceptance_criteria(issue):
            requirement_quality["without_acceptance_criteria"].append(item)
        if not any(canonical_issue_type(label) for label in labels):
            requirement_quality["without_type_label"].append(item)

        if review:
            findings = review_findings(review)
            red_findings = [finding for finding in findings if finding["status"] == "red" or finding["severity"] == "high"]
            qa_findings = [finding for finding in findings if "QA" in finding["agent"] and review.get("overall_status") in {"yellow", "red"}]
            risk_findings = [
                finding for finding in findings
                if any(token in f"{finding['title']} {finding['description']} {finding['recommendation']}".lower() for token in ["risk", "dependency", "blocker", "rollback", "security"])
            ]
            if red_findings:
                blockers.append({**item, "reason": red_findings[0]["title"], "source": "AI Review"})
            if qa_findings:
                requirement_quality["yellow_red_qa_review"].append(item)
            if risk_findings:
                requirement_quality["risks_or_dependencies_detected"].append(item)
            if review.get("overall_status") in {"yellow", "red"} and findings:
                requirement_quality["missing_info"].append(item)
            for finding in findings:
                if finding["severity"] == "high" or finding["status"] == "red" or "risk" in f"{finding['title']} {finding['description']}".lower():
                    top_risks.append({
                        "risk": finding["title"],
                        "description": finding["description"],
                        "severity": finding["severity"],
                        "source": "AI Review",
                        "agent": finding["agent"],
                        "issue_key": key,
                        "issue_title": issue.name,
                        "issue_url": issue_url(issue),
                        "module": ", ".join(item["modules"]) or "Not available",
                        "suggested_action": finding["recommendation"] or "Review with owner.",
                    })
            if review.get("overall_status") == "red":
                decisions_needed.append({
                    "decision": f"Resolve red AI review for {key}",
                    "why": "A red review means the task may be blocked, contradictory, or missing critical delivery information.",
                    "issue_key": key,
                    "issue_url": issue_url(issue),
                    "recommended_owner": "Delivery Manager / task owner",
                    "action": "Open the issue, inspect findings, and decide whether to refine, split, or park the task.",
                })

        for module in item["modules"] or ["No module"]:
            signal = module_signals.setdefault(module, {"module": module, "green": 0, "yellow": 0, "red": 0, "unreviewed": 0, "blockers": 0})
            if review:
                status = review.get("overall_status") or "unknown"
                if status in {"green", "yellow", "red"}:
                    signal[status] += 1
                if status == "red":
                    signal["blockers"] += 1
            else:
                signal["unreviewed"] += 1

    for signal in open_signals:
        signal_type = signal.get("type")
        issue_key_value = signal.get("related_issue_key") or "n/a"
        signal_issue_url = f"http://localhost:8080/aigile/browse/{issue_key_value}/" if issue_key_value != "n/a" else "#"
        signal_text = signal.get("text") or ""
        signal_item = {
            "key": issue_key_value,
            "title": signal_text,
            "url": signal_issue_url,
            "module": signal.get("module") or "Not available",
            "reason": signal_text,
            "source": signal.get("source") or "mattermost_thread",
            "signal_id": signal.get("id"),
            "severity": signal.get("severity") or "medium",
            "status": signal.get("status") or "open",
        }
        if signal_type == "risk":
            top_risks.append({
                "risk": signal_text,
                "description": signal.get("suggested_action") or "",
                "severity": signal.get("severity") or "medium",
                "source": signal.get("source") or "mattermost_thread",
                "agent": "Task thread",
                "issue_key": issue_key_value,
                "issue_title": signal_text,
                "issue_url": signal_issue_url,
                "module": signal.get("module") or "Not available",
                "suggested_action": signal.get("suggested_action") or "Assign an owner and define mitigation.",
            })
        elif signal_type == "blocker":
            blockers.append(signal_item)
        elif signal_type == "dependency":
            top_risks.append({
                "risk": f"Dependency: {signal_text}",
                "description": signal.get("suggested_action") or "",
                "severity": signal.get("severity") or "medium",
                "source": signal.get("source") or "mattermost_thread",
                "agent": "Task thread",
                "issue_key": issue_key_value,
                "issue_title": signal_text,
                "issue_url": signal_issue_url,
                "module": signal.get("module") or "Not available",
                "suggested_action": signal.get("suggested_action") or "Confirm dependency owner and date.",
            })
        elif signal_type == "decision":
            decisions_needed.append({
                "decision": signal_text,
                "why": "Captured from Mattermost task thread.",
                "issue_key": issue_key_value,
                "issue_url": signal_issue_url,
                "recommended_owner": "Delivery Manager / task owner",
                "action": signal.get("suggested_action") or "Record the decision and communicate impact.",
                "signal_id": signal.get("id"),
            })
        elif signal_type == "question":
            open_questions.append(signal_item)
        elif signal_type == "action_item":
            action_items.append(signal_item)

    has_critical_signal = any(signal.get("severity") == "critical" for signal in open_signals)
    red = status_counts["red"]
    yellow = status_counts["yellow"]
    overall = "red" if red or has_critical_signal else "yellow" if yellow or unreviewed or open_signals else "green"
    main_findings = []
    if red:
        main_findings.append(f"{red} reviewed task(s) have red AI review.")
    if has_critical_signal:
        main_findings.append("Open Mattermost task thread signals include a blocker or critical severity item.")
    if yellow:
        main_findings.append(f"{yellow} reviewed task(s) have yellow AI review.")
    if unreviewed:
        main_findings.append(f"{len(unreviewed)} task(s) have no AI review yet.")
    if requirement_quality["without_acceptance_criteria"]:
        main_findings.append(f"{len(requirement_quality['without_acceptance_criteria'])} task(s) may be missing acceptance criteria.")
    if not main_findings:
        main_findings.append("Reviewed delivery scope looks healthy based on available AIGILE signals.")

    suggested_actions = []
    if blockers:
        suggested_actions.append(f"Start with {blockers[0]['key']}: unresolved red review needs attention.")
    if requirement_quality["without_type_label"]:
        suggested_actions.append("Add type labels to untyped tasks so Agent Router can select the right review agents.")
    if requirement_quality["without_acceptance_criteria"]:
        suggested_actions.append("Run refinement on tasks missing acceptance criteria.")
    if top_risks:
        suggested_actions.append(f"Assign an owner for risk: {top_risks[0]['risk']}.")
    if open_questions:
        suggested_actions.append(f"Clarify open question for {open_questions[0]['key']}: {open_questions[0]['title']}.")
    if action_items:
        suggested_actions.append(f"Follow up action item for {action_items[0]['key']}: {action_items[0]['title']}.")
    if unreviewed:
        suggested_actions.append("Run AI analysis for the highest-priority unreviewed tasks.")
    if not suggested_actions:
        suggested_actions.append("No urgent delivery action detected from available data.")

    return {
        "ok": True,
        "created_at": utc_now_iso(),
        "project": project.name,
        "project_identifier": project.identifier,
        "overall_status": overall,
        "morning_brief": {
            "status": overall,
            "findings": main_findings[:5],
            "attention_today": suggested_actions[:3],
            "mode": "rule_based",
        },
        "delivery_health": {
            "overall": overall,
            "reviewed_total": len(reviewed),
            "unreviewed_total": len(unreviewed),
            "status_counts": status_counts,
            "red_findings_total": len(blockers),
            "waiting_human_approval": sum(1 for event in apply_events if event.get("status") in {"pending", "draft"}),
        },
        "top_risks": top_risks[:10],
        "blockers": blockers[:10],
        "requirement_quality": {
            key: {"count": len(value), "items": value[:10]}
            for key, value in requirement_quality.items()
        },
        "module_signals": sorted(module_signals.values(), key=lambda item: (item["red"], item["yellow"], item["unreviewed"]), reverse=True),
        "decisions_needed": decisions_needed[:10],
        "open_questions": open_questions[:10],
        "action_items": action_items[:10],
        "delivery_signals": {
            "total": len(delivery_signals),
            "open": len(open_signals),
            "items": open_signals[-20:],
        },
        "changes_since_yesterday": {
            "available": False,
            "message": "Historical comparison is not available yet.",
            "structure_ready": True,
        },
        "suggested_actions": suggested_actions[:8],
        "data_sources": {
            "plane_issues": True,
            "ai_review_history": REVIEW_HISTORY_PATH.exists(),
            "ai_apply_history": APPLY_HISTORY_PATH.exists(),
            "mattermost_task_thread_memory": TASK_CHAT_CONTEXT_PATH.exists(),
            "delivery_signals": DELIVERY_SIGNALS_PATH.exists(),
            "rag_decision_log": "Not available in this dashboard MVP",
        },
    }


def render_issue_link(item: dict) -> str:
    return f'<a href="{escape(str(item.get("url") or "#"))}" target="_blank">{escape(str(item.get("key") or ""))}</a>'


def status_badge(status: str) -> str:
    status = (status or "unknown").lower()
    cls = status if status in {"green", "yellow", "red"} else "unknown"
    return f'<span class="badge {cls}">{escape(status.upper())}</span>'


def brief_item_text(item: dict, *keys: str, fallback: str = "") -> str:
    for key in keys:
        value = item.get(key)
        if value:
            return str(value)
    return fallback


def trend_direction(delta: int, negative_when_up: bool = False) -> dict:
    if delta == 0:
        return {"direction": "flat", "symbol": "=", "class": "neutral", "label": "no change"}
    if negative_when_up:
        good = delta < 0
    else:
        good = delta > 0
    return {
        "direction": "up" if delta > 0 else "down",
        "symbol": "up" if delta > 0 else "down",
        "class": "good" if good else "bad",
        "label": f"{'+' if delta > 0 else ''}{delta} vs previous sync",
    }


def build_kanban_metrics(report: dict, health_index: dict) -> dict:
    health = report.get("delivery_health") or {}
    signals = report.get("delivery_signals") or {}
    quality = report.get("requirement_quality") or {}
    blockers_count = len(report.get("blockers") or [])
    open_signals = int(signals.get("open") or 0)
    reviewed = int(health.get("reviewed_total") or 0)
    unreviewed = int(health.get("unreviewed_total") or 0)
    missing_ac = int((quality.get("without_acceptance_criteria") or {}).get("count") or 0)
    risk_count = len(report.get("top_risks") or [])
    throughput = max(2, reviewed + 4)
    wip = max(1, unreviewed + blockers_count + open_signals // 3)
    lead_time = min(45, 9 + blockers_count * 3 + missing_ac + open_signals // 4)
    cycle_time = min(30, 5 + blockers_count * 2 + open_signals // 6)
    blocked_time = min(20, 2 + blockers_count * 2 + risk_count // 4)
    flow_efficiency = clamp_int(62 - blockers_count * 5 - open_signals // 2 + reviewed, 8, 95)
    aging = min(35, 7 + unreviewed // 2 + blockers_count * 3)
    rework_rate = min(55, 8 + missing_ac * 2 + blockers_count * 3)
    metrics = [
        {
            "id": "flow-throughput",
            "label": "Throughput",
            "value": throughput,
            "unit": "items/week",
            "delta": -3,
            "negative_when_up": False,
            "summary": "How many work items the team is finishing per week in the current demo scope.",
            "history": ["6 months ago: 4 items/week", "Previous sync: 11 items/week", f"Today: {throughput} items/week", "Signal: output is falling while risk load is growing"],
        },
        {
            "id": "flow-lead-time",
            "label": "Lead time",
            "value": lead_time,
            "unit": "days",
            "delta": 6,
            "negative_when_up": True,
            "summary": "Elapsed time from request entering the system to completion.",
            "history": ["6 months ago: 12 days", f"Previous sync: {max(1, lead_time - 6)} days", f"Today: {lead_time} days", "Signal: decisions and blockers are stretching delivery"],
        },
        {
            "id": "flow-cycle-time",
            "label": "Cycle time",
            "value": cycle_time,
            "unit": "days",
            "delta": 4,
            "negative_when_up": True,
            "summary": "Time from actual start of work to Done.",
            "history": ["6 months ago: 6 days", f"Previous sync: {max(1, cycle_time - 4)} days", f"Today: {cycle_time} days", "Signal: started work is spending longer inside delivery"],
        },
        {
            "id": "flow-wip",
            "label": "WIP",
            "value": wip,
            "unit": "items",
            "delta": 5,
            "negative_when_up": True,
            "summary": "Work currently in progress or waiting inside the delivery flow.",
            "history": ["6 months ago: 8 WIP items", f"Previous sync: {max(1, wip - 5)} WIP items", f"Today: {wip} WIP items", "Signal: too much work is open at once"],
        },
        {
            "id": "flow-blocked-time",
            "label": "Blocked time",
            "value": blocked_time,
            "unit": "days",
            "delta": 3,
            "negative_when_up": True,
            "summary": "Estimated time work spends blocked by decisions, dependencies, or unclear requirements.",
            "history": ["6 months ago: 2 blocked days", f"Previous sync: {max(0, blocked_time - 3)} blocked days", f"Today: {blocked_time} blocked days", "Signal: blockers need leadership action"],
        },
        {
            "id": "flow-efficiency",
            "label": "Flow efficiency",
            "value": flow_efficiency,
            "unit": "%",
            "delta": -9,
            "negative_when_up": False,
            "summary": "Share of time spent in value-producing work versus waiting, blocked, or rework states.",
            "history": ["6 months ago: 48%", f"Previous sync: {min(95, flow_efficiency + 9)}%", f"Today: {flow_efficiency}%", "Signal: waiting time is eating delivery capacity"],
        },
        {
            "id": "flow-aging-wip",
            "label": "WIP aging",
            "value": aging,
            "unit": "days",
            "delta": 5,
            "negative_when_up": True,
            "summary": "Oldest active work age, useful for spotting items silently stuck in progress.",
            "history": ["6 months ago: 9 days", f"Previous sync: {max(1, aging - 5)} days", f"Today: {aging} days", "Signal: at least one active item is aging beyond a healthy threshold"],
        },
        {
            "id": "flow-rework-rate",
            "label": "Rework rate",
            "value": rework_rate,
            "unit": "%",
            "delta": 7,
            "negative_when_up": True,
            "summary": "Share of work likely to return for clarification, QA, or requirement correction.",
            "history": ["6 months ago: 14%", f"Previous sync: {max(0, rework_rate - 7)}%", f"Today: {rework_rate}%", "Signal: weak acceptance criteria and review findings are creating rework"],
        },
    ]
    for metric in metrics:
        metric["trend"] = trend_direction(int(metric["delta"]), bool(metric["negative_when_up"]))
    return {
        "mode": "demo_previous_sync",
        "title": "Kanban Flow Metrics",
        "subtitle": "Demo flow metrics: throughput, lead time, cycle time, WIP, blocked time, flow efficiency, aging, and rework compared with the previous leadership sync.",
        "metrics": metrics,
        "summary": "Throughput is falling while lead time, cycle time, WIP, blocked time, aging, and rework are rising.",
    }


def build_executive_insight(
    health_index: dict,
    top_risks: list[dict],
    blockers: list[dict],
    decisions: list[dict],
    quality_issues: list[dict],
    kanban_metrics: dict,
    source_counts: dict,
) -> dict:
    top_risk = top_risks[0] if top_risks else {}
    top_blocker = blockers[0] if blockers else {}
    top_decision = decisions[0] if decisions else {}
    weakest_flow = None
    for metric in kanban_metrics.get("metrics") or []:
        trend = metric.get("trend") or {}
        if trend.get("class") == "bad":
            weakest_flow = metric
            break
    situation = (
        f"Delivery health is {health_index.get('score')}/100 with schedule confidence {health_index.get('schedule_confidence')}/100. "
        f"The system sees {source_counts.get('delivery_signals_open') or 0} open team/thread signals, "
        f"{len(blockers)} blocker(s), and {len(quality_issues)} requirement quality issue(s)."
    )
    if health_index.get("score", 100) <= 15:
        business_impact = "Current delivery state is close to critical: unresolved blockers and unclear requirements can push scope, quality, or demo readiness off track."
    elif health_index.get("status") == "red":
        business_impact = "Delivery needs leadership attention before the team can move predictably."
    else:
        business_impact = "Delivery is currently manageable, but the visible signals still need regular review."
    decision_focus = []
    if top_blocker:
        decision_focus.append(f"Unblock {top_blocker.get('issue_key') or 'the top blocker'}: {top_blocker.get('summary') or top_blocker.get('issue_title') or 'blocker needs attention'}.")
    if top_decision:
        decision_focus.append(f"Make decision for {top_decision.get('issue_key') or 'open decision'}: {top_decision.get('summary') or 'decision needed'}.")
    if top_risk:
        decision_focus.append(f"Assign owner for top risk {top_risk.get('issue_key') or ''}: {top_risk.get('summary') or 'risk needs mitigation'}.")
    next_24h = []
    if blockers:
        next_24h.append("Resolve or explicitly accept the top blocker before expanding scope.")
    if decisions:
        next_24h.append("Turn open decisions into named owners and deadlines.")
    if quality_issues:
        next_24h.append("Refine tasks with missing acceptance criteria before they enter active delivery.")
    if weakest_flow:
        next_24h.append(f"Review {weakest_flow.get('label')} trend: {weakest_flow.get('trend', {}).get('label')}.")
    watchlist = []
    if weakest_flow:
        watchlist.append(f"{weakest_flow.get('label')}: {weakest_flow.get('value')}{weakest_flow.get('unit')} ({weakest_flow.get('trend', {}).get('label')})")
    if top_risk:
        watchlist.append(f"Top risk: {top_risk.get('issue_key') or 'n/a'}")
    if source_counts.get("delivery_signals_open"):
        watchlist.append(f"Open team signals: {source_counts.get('delivery_signals_open')}")
    return {
        "situation": situation,
        "business_impact": business_impact,
        "decision_focus": decision_focus[:3] or ["No immediate leadership decision detected from available data."],
        "next_24h": next_24h[:4] or ["Keep monitoring current delivery signals."],
        "watchlist": watchlist[:4] or ["No watchlist items detected."],
    }


def issue_ref(key: str | None, title: str | None = None) -> str:
    if not key:
        return title or "n/a"
    return f"{key}: {title}" if title else key


def issue_key_url(key: str | None, fallback: str | None = None) -> str:
    if fallback:
        return fallback
    if not key or key == "n/a":
        return "#"
    return f"http://localhost:8080/aigile/browse/{key}/"


def render_brief_issue_link(item: dict) -> str:
    key = item.get("issue_key")
    title = item.get("issue_title")
    if title and len(str(title)) > 80:
        title = None
    label = issue_ref(key, title)
    url = issue_key_url(key, item.get("issue_url") or item.get("url"))
    if url == "#":
        return escape(label)
    return f'<a href="{escape(url)}" target="_blank" rel="noreferrer">{escape(label)}</a>'


def clamp_int(value: int | float, minimum: int = 0, maximum: int = 100) -> int:
    return max(minimum, min(maximum, int(round(value))))


def severity_points(severity: str | None) -> int:
    return {
        "critical": 18,
        "high": 12,
        "medium": 6,
        "low": 2,
    }.get(str(severity or "").lower(), 4)


def build_health_index(report: dict) -> dict:
    health = report.get("delivery_health") or {}
    counts = health.get("status_counts") or {}
    rq = report.get("requirement_quality") or {}
    delivery_signals = report.get("delivery_signals") or {}
    red = int(counts.get("red") or 0)
    yellow = int(counts.get("yellow") or 0)
    unreviewed = int(health.get("unreviewed_total") or 0)
    blockers_count = len(report.get("blockers") or [])
    risks = report.get("top_risks") or []
    open_signals = delivery_signals.get("items") or []
    missing_ac = int((rq.get("without_acceptance_criteria") or {}).get("count") or 0)
    missing_type = int((rq.get("without_type_label") or {}).get("count") or 0)
    missing_info = int((rq.get("missing_info") or {}).get("count") or 0)
    risk_penalty = sum(severity_points(risk.get("severity")) for risk in risks[:8])
    signal_penalty = sum(severity_points(signal.get("severity")) for signal in open_signals[:8])
    score = 100
    score -= red * 14
    score -= yellow * 5
    score -= blockers_count * 12
    score -= unreviewed * 2
    score -= missing_ac * 3
    score -= missing_type * 2
    score -= missing_info * 2
    score -= min(24, risk_penalty)
    score -= min(22, signal_penalty)
    score = clamp_int(score, int(os.environ.get("AIGILE_HEALTH_INDEX_FLOOR", "60")), 100)
    status = "green" if score >= 80 else "yellow" if score >= 55 else "red"
    schedule_confidence = clamp_int(
        100 - blockers_count * 18 - red * 10 - yellow * 4 - min(30, risk_penalty) - unreviewed * 2,
        int(os.environ.get("AIGILE_SCHEDULE_CONFIDENCE_FLOOR", "55")),
        100,
    )
    schedule_status = "green" if schedule_confidence >= 80 else "yellow" if schedule_confidence >= 55 else "red"
    drivers = []
    if blockers_count:
        drivers.append(f"{blockers_count} blocker(s) or red impediment(s)")
    if red:
        drivers.append(f"{red} red AI review task(s)")
    if yellow:
        drivers.append(f"{yellow} yellow AI review task(s)")
    if unreviewed:
        drivers.append(f"{unreviewed} task(s) without AI review")
    if missing_ac:
        drivers.append(f"{missing_ac} task(s) may miss acceptance criteria")
    if delivery_signals.get("open"):
        drivers.append(f"{delivery_signals.get('open')} open meeting/thread signal(s)")
    if not drivers:
        drivers.append("No major negative signals in available data")
    if schedule_confidence < 55:
        schedule_summary = "Schedule is at risk based on blockers, red reviews, and open risks."
    elif schedule_confidence < 80:
        schedule_summary = "Schedule needs attention: some risks or unclear tasks may affect delivery."
    else:
        schedule_summary = "Schedule confidence looks healthy based on available signals."
    return {
        "score": score,
        "status": status,
        "label": "Healthy" if status == "green" else "Needs attention" if status == "yellow" else "At risk",
        "drivers": drivers[:6],
        "schedule_confidence": schedule_confidence,
        "schedule_status": schedule_status,
        "schedule_summary": schedule_summary,
        "formula": "100 - blockers - red/yellow reviews - open risks - missing AC/type - unreviewed work",
    }


def score_brief_risk(item: dict) -> int:
    source = str(item.get("source") or "").lower()
    score = severity_points(item.get("severity"))
    if item.get("issue_key"):
        score += 2
    if "mattermost" in source or "thread" in source:
        score += 4
    if "blocker" in str(item.get("summary") or "").lower():
        score += 4
    return score


def build_daily_delivery_brief(report: dict | None = None) -> dict:
    report = report or build_delivery_intelligence_report()
    rq = report.get("requirement_quality") or {}
    delivery_signals = report.get("delivery_signals") or {}
    top_risks = []
    for risk in report.get("top_risks") or []:
        top_risks.append({
            "issue_key": risk.get("issue_key"),
            "issue_title": risk.get("issue_title"),
            "severity": risk.get("severity") or "medium",
            "source": risk.get("source") or risk.get("agent") or "unknown",
            "summary": brief_item_text(risk, "risk", "description", fallback="Risk without summary"),
            "suggested_action": risk.get("suggested_action") or "Review with owner.",
            "issue_url": issue_key_url(risk.get("issue_key"), risk.get("issue_url")),
        })
    for signal in delivery_signals.get("items") or []:
        if signal.get("type") in {"risk", "dependency", "blocker"}:
            top_risks.append({
                "issue_key": signal.get("related_issue_key"),
                "issue_title": signal.get("module") or "",
                "severity": signal.get("severity") or "medium",
                "source": signal.get("source") or "mattermost_thread",
                "summary": signal.get("text") or "Meeting/thread signal without summary",
                "suggested_action": signal.get("suggested_action") or "Review with owner.",
                "issue_url": issue_key_url(signal.get("related_issue_key")),
            })
    for item in top_risks:
        item["risk_score"] = score_brief_risk(item)
    top_risks = sorted(top_risks, key=lambda item: item.get("risk_score") or 0, reverse=True)

    blockers = []
    for item in report.get("blockers") or []:
        blockers.append({
            "issue_key": item.get("key"),
            "issue_title": item.get("title"),
            "severity": item.get("severity") or "high",
            "source": item.get("source") or "AI Review",
            "summary": item.get("reason") or item.get("title") or "Blocker without summary",
            "suggested_action": "Decide owner and unblock path today.",
            "issue_url": issue_key_url(item.get("key"), item.get("url")),
        })

    decisions = []
    for decision in report.get("decisions_needed") or []:
        decisions.append({
            "issue_key": decision.get("issue_key"),
            "source": "Delivery Intelligence",
            "summary": decision.get("decision") or "Decision needed",
            "why": decision.get("why") or "",
            "suggested_action": decision.get("action") or "Make or assign the decision today.",
            "issue_url": issue_key_url(decision.get("issue_key"), decision.get("issue_url")),
        })

    quality_issues = []
    quality_labels = {
        "without_acceptance_criteria": "Missing acceptance criteria",
        "without_type_label": "Missing type label",
        "missing_info": "Missing critical task information",
        "yellow_red_qa_review": "QA review needs attention",
        "risks_or_dependencies_detected": "Risk or dependency detected",
    }
    for key, label in quality_labels.items():
        block = rq.get(key) or {}
        for item in block.get("items") or []:
            quality_issues.append({
                "issue_key": item.get("key"),
                "issue_title": item.get("title"),
                "summary": label,
                "source": "Plane / AI Review",
                "issue_url": issue_key_url(item.get("key"), item.get("url")),
            })

    data_notes = []
    if not top_risks:
        data_notes.append("No critical risks found in available data.")
    if not blockers:
        data_notes.append("No blockers found in available data.")
    if not decisions:
        data_notes.append("No management decisions detected in available data.")
    if not delivery_signals.get("total"):
        data_notes.append("No meeting/thread signals available.")
    changes = report.get("changes_since_yesterday") or {}
    if not changes.get("available"):
        data_notes.append(changes.get("message") or "Historical comparison is not available yet.")

    findings = (report.get("morning_brief") or {}).get("findings") or []
    actions = report.get("suggested_actions") or []
    executive_summary = " ".join(findings[:3]).strip()
    if not executive_summary:
        executive_summary = "No urgent delivery action detected from available data."
    health_index = build_health_index(report)
    if health_index["score"] <= 15:
        executive_summary = f"Project health is near critical ({health_index['score']}/100). Multiple risks and blockers require management decisions today. {executive_summary}"
    elif health_index["status"] == "red":
        executive_summary = f"Project health is at risk ({health_index['score']}/100). {executive_summary}"
    elif health_index["status"] == "yellow":
        executive_summary = f"Project health needs attention ({health_index['score']}/100). {executive_summary}"
    else:
        executive_summary = f"Project health looks healthy ({health_index['score']}/100). {executive_summary}"
    kanban_metrics = build_kanban_metrics(report, health_index)
    source_counts = {
        "reviewed_total": (report.get("delivery_health") or {}).get("reviewed_total") or 0,
        "unreviewed_total": (report.get("delivery_health") or {}).get("unreviewed_total") or 0,
        "delivery_signals_total": delivery_signals.get("total") or 0,
        "delivery_signals_open": delivery_signals.get("open") or 0,
    }
    executive_insight = build_executive_insight(
        health_index,
        top_risks[:5],
        blockers[:5],
        decisions[:5],
        quality_issues[:10],
        kanban_metrics,
        source_counts,
    )

    return {
        "ok": True,
        "date": datetime.now(timezone.utc).date().isoformat(),
        "created_at": utc_now_iso(),
        "project": report.get("project") or "AIGILE",
        "overall_status": report.get("overall_status") or "unknown",
        "health_index": health_index,
        "kanban_metrics": kanban_metrics,
        "analytics_modes": ["Executive", "Kanban", "Risks", "Team Signals", "Data Quality"],
        "executive_summary": executive_summary,
        "executive_insight": executive_insight,
        "top_5_risks": top_risks[:5],
        "top_blockers": blockers[:5],
        "decisions_needed": decisions[:5],
        "requirement_quality_issues": quality_issues[:10],
        "changes_since_yesterday": {
            "available": bool(changes.get("available")),
            "summary": changes.get("message") or "Historical comparison is not available yet.",
        },
        "suggested_actions_for_today": actions[:5] or ["No urgent delivery action detected from available data."],
        "data_notes": data_notes,
        "source_counts": source_counts,
        "source_report_created_at": report.get("created_at"),
    }


def render_brief_list(items: list[dict], empty: str, fields: tuple[str, ...]) -> str:
    if not items:
        return f"<li class=\"muted\">{escape(empty)}</li>"
    rows = []
    for item in items:
        prefix = render_brief_issue_link(item)
        details = " | ".join(str(item.get(field) or "") for field in fields if item.get(field))
        rows.append(f"<li><strong>{prefix}</strong><br><span>{escape(details or item.get('summary') or '')}</span></li>")
    return "".join(rows)


def render_daily_delivery_brief(brief: dict) -> str:
    notes = "".join(f"<li>{escape(str(note))}</li>" for note in brief.get("data_notes") or [])
    if not notes:
        notes = '<li>No data gaps detected in available sources.</li>'
    actions = "".join(f"<li>{escape(str(action))}</li>" for action in brief.get("suggested_actions_for_today") or [])
    health = brief.get("health_index") or {}
    health_score = int(health.get("score") or 0)
    schedule_score = int(health.get("schedule_confidence") or 0)
    critical_class = " critical" if health_score <= 15 else ""
    health_panel_class = "critical-alert" if health_score <= 15 else "warning-alert" if str(health.get("status") or "") == "yellow" else "healthy-alert"
    critical_title = "Near Critical" if health_score <= 15 else str(health.get("label") or "")
    critical_message = (
        "Health Index shows remaining delivery health. This is an executive warning: risks, blockers, and weak requirements require decisions today."
        if health_score <= 15
        else "Health Index summarizes current delivery readiness from available signals."
    )
    mode_links = "".join(
        f'<a class="mode" href="#{escape(str(mode).lower().replace(" ", "-"))}">{escape(str(mode))}</a>'
        for mode in brief.get("analytics_modes") or ["Executive", "Risks", "Team Signals", "Data Quality"]
    )
    drivers = "".join(f"<li>{escape(str(driver))}</li>" for driver in health.get("drivers") or [])
    risk_rows = []
    for risk in brief.get("top_5_risks") or []:
        issue_link = render_brief_issue_link(risk)
        risk_rows.append(
            f"""
            <tr>
              <td><strong>{escape(str(risk.get("summary") or ""))}</strong><div class="muted small">{escape(str(risk.get("suggested_action") or ""))}</div></td>
              <td>{escape(str(risk.get("severity") or "medium")).upper()}</td>
              <td>{escape(str(risk.get("source") or ""))}</td>
              <td>{issue_link}</td>
              <td>{escape(str(risk.get("risk_score") or ""))}</td>
            </tr>
            """
        )
    if not risk_rows:
        risk_rows.append('<tr><td colspan="5" class="muted">No critical risks found in available data.</td></tr>')
    blocker_count = len(brief.get("top_blockers") or [])
    decision_count = len(brief.get("decisions_needed") or [])
    quality_count = len(brief.get("requirement_quality_issues") or [])
    source_counts = brief.get("source_counts") or {}
    insight = brief.get("executive_insight") or {}
    decision_focus = "".join(f"<li>{escape(str(item))}</li>" for item in insight.get("decision_focus") or [])
    next_24h = "".join(f"<li>{escape(str(item))}</li>" for item in insight.get("next_24h") or [])
    watchlist = "".join(f"<li>{escape(str(item))}</li>" for item in insight.get("watchlist") or [])
    kanban = brief.get("kanban_metrics") or {}
    trend_cards = []
    for metric in kanban.get("metrics") or []:
        trend = metric.get("trend") or {}
        history = "".join(f"<li>{escape(str(item))}</li>" for item in metric.get("history") or [])
        trend_cards.append(
            f"""
            <details class="trend-card">
              <summary>
                <span>
                  <span class="metric-label">{escape(str(metric.get("label") or ""))}</span>
                  <strong>{escape(str(metric.get("value") or 0))}<small>{escape(str(metric.get("unit") or ""))}</small></strong>
                </span>
                <mark class="trend {escape(str(trend.get("class") or "neutral"))}">{escape(str(trend.get("symbol") or "="))} {escape(str(trend.get("label") or ""))}</mark>
              </summary>
              <p>{escape(str(metric.get("summary") or ""))}</p>
              <ul>{history}</ul>
            </details>
            """
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AIGILE Daily Delivery Brief</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101214;
      --panel: #171a1f;
      --line: #2b3038;
      --text: #e7e9ee;
      --muted: #a1a8b3;
      --green: #24c36b;
      --yellow: #f0c94a;
      --red: #ff5d5d;
      --blue: #78b7ff;
      --purple: #b79cff;
      --danger-bg: #34181b;
      --danger-line: #8d2f3b;
    }}
    body {{ margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 32px 20px 56px; }}
    header {{ display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; margin-bottom: 20px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    p {{ color: var(--muted); margin: 0; line-height: 1.5; }}
    section, .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; margin-bottom: 14px; }}
    ul {{ margin: 0; padding-left: 20px; }}
    li {{ margin: 8px 0; }}
    a {{ color: var(--blue); text-decoration: none; }}
    .badge {{ display: inline-flex; border-radius: 999px; border: 1px solid var(--muted); color: var(--muted); padding: 4px 9px; font-size: 12px; font-weight: 800; }}
    .badge.green {{ color: var(--green); border-color: var(--green); }}
    .badge.yellow {{ color: var(--yellow); border-color: var(--yellow); }}
    .badge.red {{ color: var(--red); border-color: var(--red); }}
    .critical-alert {{ border-color: var(--danger-line); background: linear-gradient(135deg, var(--danger-bg), #171a1f 70%); }}
    .critical-alert h2 {{ color: #ff8a8a; }}
    .warning-alert {{ border-color: rgba(240, 201, 74, 0.72); background: linear-gradient(135deg, rgba(75, 61, 23, 0.78), #171a1f 70%); }}
    .warning-alert h2 {{ color: var(--yellow); }}
    .healthy-alert {{ border-color: rgba(36, 195, 107, 0.55); background: linear-gradient(135deg, rgba(17, 61, 42, 0.68), #171a1f 70%); }}
    .healthy-alert h2 {{ color: var(--green); }}
    .muted {{ color: var(--muted); }}
    .small {{ font-size: 12px; margin-top: 4px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: start; }}
    .grid > section {{ align-self: start; }}
    .hero {{ display: grid; grid-template-columns: minmax(260px, 0.85fr) 1.15fr; gap: 14px; align-items: start; }}
    .index-card {{ display: grid; grid-template-columns: 150px 1fr; gap: 16px; align-items: center; }}
    .summary-card {{ display: grid; gap: 12px; }}
    .summary-lead {{ font-size: 15px; color: var(--text); }}
    .summary-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
    .summary-box {{ background: #1d222b; border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
    .summary-box h3 {{ margin: 0 0 8px; font-size: 13px; color: var(--muted); letter-spacing: 0; }}
    .summary-box ul {{ padding-left: 18px; }}
    .summary-box li {{ font-size: 13px; }}
    .gauge {{
      width: 140px;
      aspect-ratio: 1;
      border-radius: 50%;
      display: grid;
      place-items: center;
      background: conic-gradient(var(--blue) 0 {health_score}%, #2a3039 {health_score}% 100%);
      box-shadow: inset 0 0 0 1px var(--line);
    }}
    .gauge.critical {{ background: conic-gradient(var(--red) 0 {health_score}%, #3b171b {health_score}% 100%); box-shadow: 0 0 0 1px var(--danger-line), 0 0 34px rgba(255, 93, 93, 0.18); }}
    .warning-alert .gauge {{ background: conic-gradient(var(--yellow) 0 {health_score}%, #3c331b {health_score}% 100%); box-shadow: 0 0 0 1px rgba(240, 201, 74, 0.62), 0 0 30px rgba(240, 201, 74, 0.12); }}
    .healthy-alert .gauge {{ background: conic-gradient(var(--green) 0 {health_score}%, #183327 {health_score}% 100%); box-shadow: 0 0 0 1px rgba(36, 195, 107, 0.45); }}
    .gauge-inner {{
      width: 104px;
      aspect-ratio: 1;
      border-radius: 50%;
      background: var(--panel);
      display: grid;
      place-items: center;
      text-align: center;
      border: 1px solid var(--line);
    }}
    .gauge strong {{ font-size: 30px; line-height: 1; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }}
    .metric {{ background: #1d222b; border: 1px solid var(--line); border-radius: 8px; padding: 12px; min-height: 82px; }}
    .metric span {{ color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 8px; font-size: 24px; }}
    .bar {{ height: 10px; background: #2a3039; border-radius: 999px; overflow: hidden; margin-top: 10px; }}
    .bar > i {{ display: block; height: 100%; width: {schedule_score}%; background: var(--purple); }}
    .modes {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }}
    .mode {{ border: 1px solid var(--line); background: #1d222b; color: var(--text); border-radius: 999px; padding: 8px 11px; font-size: 13px; }}
    .trend-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }}
    details.trend-card {{ background: #1d222b; border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
    details.trend-card summary {{ cursor: pointer; list-style: none; display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; flex-wrap: wrap; }}
    details.trend-card summary::-webkit-details-marker {{ display: none; }}
    .metric-label {{ display: block; color: var(--muted); font-size: 12px; margin-bottom: 8px; }}
    .trend-card strong {{ font-size: 24px; }}
    .trend-card small {{ font-size: 13px; color: var(--muted); margin-left: 3px; }}
    .trend {{ border-radius: 999px; padding: 4px 8px; font-size: 12px; font-weight: 800; white-space: normal; text-align: center; max-width: 100%; background: transparent; }}
    .trend.good {{ color: var(--green); border: 1px solid rgba(36, 195, 107, 0.5); }}
    .trend.bad {{ color: var(--red); border: 1px solid rgba(255, 93, 93, 0.55); }}
    .trend.neutral {{ color: var(--muted); border: 1px solid var(--line); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ color: var(--muted); font-weight: 700; }}
    tr:last-child td {{ border-bottom: none; }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }}
    .button {{ display: inline-flex; border: 1px solid var(--line); background: #1d222b; color: var(--text); border-radius: 8px; padding: 9px 12px; font-size: 14px; }}
    @media (max-width: 900px) {{ header, .grid, .hero, .metric-grid, .trend-grid, .summary-grid {{ display: block; }} .index-card {{ grid-template-columns: 1fr; }} .metric, details.trend-card, .summary-box {{ margin-bottom: 10px; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Daily Delivery Brief</h1>
        <p>{escape(str(brief.get("project") or "AIGILE"))} · {escape(str(brief.get("date") or ""))} · Generated {escape(str(brief.get("created_at") or ""))}</p>
      </div>
      {status_badge(str(brief.get("overall_status") or "unknown"))}
    </header>

    <nav class="modes" aria-label="Brief modes">{mode_links}</nav>

    <div class="hero" id="executive">
      <section class="index-card {health_panel_class}">
        <div class="gauge{critical_class}" aria-label="Project Health Index {health_score} of 100">
          <div class="gauge-inner"><strong>{escape(str(health_score))}</strong><span class="muted">/100</span></div>
        </div>
        <div>
          <h2>Project Health Index: {escape(critical_title)}</h2>
          <p><strong>{escape(str(health_score))}/100 health remaining.</strong> {escape(critical_message)}</p>
          <p class="small">{escape(str(health.get("formula") or ""))}.</p>
          <ul>{drivers}</ul>
        </div>
      </section>
      <section class="summary-card">
        <h2>Executive Summary</h2>
        <p class="summary-lead">{escape(str(insight.get("situation") or brief.get("executive_summary") or ""))}</p>
        <div class="summary-box">
          <h3>Business impact</h3>
          <p>{escape(str(insight.get("business_impact") or ""))}</p>
          <div class="bar" aria-label="Schedule confidence {schedule_score} of 100"><i></i></div>
          <p class="small">Schedule confidence: {escape(str(schedule_score))}/100. {escape(str(health.get("schedule_summary") or ""))}</p>
        </div>
        <div class="summary-grid">
          <div class="summary-box">
            <h3>Decision focus</h3>
            <ul>{decision_focus}</ul>
          </div>
          <div class="summary-box">
            <h3>Next 24h</h3>
            <ul>{next_24h}</ul>
          </div>
        </div>
        <div class="summary-box">
          <h3>Watchlist</h3>
          <ul>{watchlist}</ul>
        </div>
      </section>
    </div>

    <div class="metric-grid">
      <div class="metric"><span>Open Thread Signals</span><strong>{escape(str(source_counts.get("delivery_signals_open") or 0))}</strong></div>
      <div class="metric"><span>Top Blockers</span><strong>{escape(str(blocker_count))}</strong></div>
      <div class="metric"><span>Decisions Needed</span><strong>{escape(str(decision_count))}</strong></div>
      <div class="metric"><span>Quality Issues</span><strong>{escape(str(quality_count))}</strong></div>
    </div>

    <section id="kanban">
      <h2>{escape(str(kanban.get("title") or "Kanban & Delivery Trends"))}</h2>
      <p>{escape(str(kanban.get("subtitle") or ""))}</p>
      <div class="trend-grid">{''.join(trend_cards)}</div>
      <p class="small">{escape(str(kanban.get("summary") or ""))}</p>
    </section>

    <section>
      <h2>Transparency Sources</h2>
      <p>Signals combine AI Review, Plane issues, Mattermost task threads, and explicit meeting/thread commands. This is meant to reduce manual reporting and make delivery risks visible early.</p>
    </section>

    <section id="risks">
      <h2>Top AI Risks</h2>
      <table>
        <thead><tr><th>Risk</th><th>Severity</th><th>Source</th><th>Issue</th><th>AI score</th></tr></thead>
        <tbody>{''.join(risk_rows)}</tbody>
      </table>
    </section>

    <div class="grid" id="team-signals">
      <section>
        <h2>Top 5 Risks Summary</h2>
        <ul>{render_brief_list(brief.get("top_5_risks") or [], "No critical risks found in available data.", ("severity", "source", "summary", "suggested_action"))}</ul>
      </section>
      <section>
        <h2>Top Blockers</h2>
        <ul>{render_brief_list(brief.get("top_blockers") or [], "No blockers found in available data.", ("severity", "source", "summary", "suggested_action"))}</ul>
      </section>
    </div>

    <div class="grid" id="data-quality">
      <section>
        <h2>Decisions Needed</h2>
        <ul>{render_brief_list(brief.get("decisions_needed") or [], "No management decisions detected in available data.", ("summary", "why", "suggested_action"))}</ul>
      </section>
      <section>
        <h2>Requirement Quality Issues</h2>
        <ul>{render_brief_list(brief.get("requirement_quality_issues") or [], "No requirement quality issues found in available data.", ("summary", "source"))}</ul>
      </section>
    </div>

    <section>
      <h2>Changes Since Yesterday</h2>
      <p>{escape(str((brief.get("changes_since_yesterday") or {}).get("summary") or "Historical comparison is not available yet."))}</p>
    </section>

    <section>
      <h2>Suggested Actions for Today</h2>
      <ul>{actions}</ul>
    </section>

    <section>
      <h2>Data Notes</h2>
      <ul>{notes}</ul>
    </section>

    <div class="actions">
      <a class="button" href="/delivery-dashboard">Delivery dashboard</a>
      <a class="button" href="/api/daily-delivery-brief">JSON brief</a>
      <a class="button" href="/dashboard">Health dashboard</a>
    </div>
  </main>
</body>
</html>"""


def format_daily_delivery_brief_mattermost(brief: dict) -> str:
    def bullet(items: list[dict], empty: str, key: str = "summary") -> str:
        if not items:
            return f"- {empty}"
        lines = []
        for item in items[:5]:
            issue = item.get("issue_key")
            prefix = f"`{issue}` " if issue else ""
            lines.append(f"- {prefix}{item.get(key) or item.get('summary') or ''}")
        return "\n".join(lines)

    actions = "\n".join(f"- {action}" for action in brief.get("suggested_actions_for_today") or [])
    notes = "\n".join(f"- {note}" for note in brief.get("data_notes") or [])
    health = brief.get("health_index") or {}
    health_line = ""
    if health:
        health_line = (
            f"\n**Project Health**\n"
            f"- Health Index: `{health.get('score')}/100` ({health.get('label') or health.get('status')})\n"
            f"- Schedule confidence: `{health.get('schedule_confidence')}/100` ({health.get('schedule_status')})\n"
        )
    return f"""**Daily Delivery Brief** · {brief.get("date")} · Status: `{brief.get("overall_status")}`

{health_line}
**Executive Summary**
{brief.get("executive_summary")}

**Top Risks**
{bullet(brief.get("top_5_risks") or [], "No critical risks found in available data.")}

**Blockers**
{bullet(brief.get("top_blockers") or [], "No blockers found in available data.")}

**Decisions Needed**
{bullet(brief.get("decisions_needed") or [], "No management decisions detected in available data.")}

**Suggested Actions**
{actions or "- No urgent delivery action detected from available data."}

**Data Notes**
{notes or "- No data gaps detected in available sources."}"""


def render_delivery_intelligence_dashboard(report: dict) -> str:
    health = report.get("delivery_health") or {}
    counts = health.get("status_counts") or {}
    brief = report.get("morning_brief") or {}
    rq = report.get("requirement_quality") or {}

    def list_items(items: list[str]) -> str:
        return "".join(f"<li>{escape(str(item))}</li>" for item in items) or "<li>Nothing urgent detected from available data.</li>"

    def issue_rows(items: list[dict], empty: str, include_reason: bool = False) -> str:
        if not items:
            return f'<tr><td colspan="{4 if include_reason else 3}" class="muted">{escape(empty)}</td></tr>'
        rows = []
        for item in items:
            reason = f"<td>{escape(str(item.get('reason') or item.get('source') or ''))}</td>" if include_reason else ""
            rows.append(
                f"""
                <tr>
                  <td>{render_issue_link(item)}</td>
                  <td>{escape(str(item.get("title") or item.get("issue_title") or ""))}</td>
                  <td>{escape(", ".join(item.get("modules") or []) or str(item.get("module") or "Not available"))}</td>
                  {reason}
                </tr>
                """
            )
        return "".join(rows)

    risk_rows = []
    for risk in report.get("top_risks") or []:
        risk_rows.append(
            f"""
            <tr>
              <td>{escape(str(risk.get("risk") or ""))}<div class="muted small">{escape(str(risk.get("description") or ""))}</div></td>
              <td>{escape(str(risk.get("severity") or "medium")).upper()}</td>
              <td>{escape(str(risk.get("agent") or risk.get("source") or ""))}</td>
              <td><a href="{escape(str(risk.get("issue_url") or "#"))}" target="_blank">{escape(str(risk.get("issue_key") or ""))}</a></td>
              <td>{escape(str(risk.get("suggested_action") or ""))}</td>
            </tr>
            """
        )
    if not risk_rows:
        risk_rows.append('<tr><td colspan="5" class="muted">No explicit high risks found in available AI reviews.</td></tr>')

    module_rows = []
    for signal in report.get("module_signals") or []:
        module_rows.append(
            f"""
            <tr>
              <td>{escape(str(signal.get("module") or "No module"))}</td>
              <td>{escape(str(signal.get("red") or 0))}</td>
              <td>{escape(str(signal.get("yellow") or 0))}</td>
              <td>{escape(str(signal.get("green") or 0))}</td>
              <td>{escape(str(signal.get("unreviewed") or 0))}</td>
            </tr>
            """
        )
    if not module_rows:
        module_rows.append('<tr><td colspan="5" class="muted">Module signal data is not available.</td></tr>')

    decision_rows = []
    for decision in report.get("decisions_needed") or []:
        decision_rows.append(
            f"""
            <tr>
              <td>{escape(str(decision.get("decision") or ""))}<div class="muted small">{escape(str(decision.get("why") or ""))}</div></td>
              <td><a href="{escape(str(decision.get("issue_url") or "#"))}" target="_blank">{escape(str(decision.get("issue_key") or ""))}</a></td>
              <td>{escape(str(decision.get("recommended_owner") or ""))}</td>
              <td>{escape(str(decision.get("action") or ""))}</td>
            </tr>
            """
        )
    if not decision_rows:
        decision_rows.append('<tr><td colspan="4" class="muted">No management decisions detected from available data.</td></tr>')

    data_sources = report.get("data_sources") or {}
    source_rows = "".join(
        f"<tr><td>{escape(str(name))}</td><td>{escape(str(value))}</td></tr>"
        for name, value in data_sources.items()
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AIGILE Delivery Intelligence</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101214;
      --panel: #171a1f;
      --panel-2: #1d222b;
      --line: #2b3038;
      --text: #e7e9ee;
      --muted: #a1a8b3;
      --green: #24c36b;
      --yellow: #f0c94a;
      --red: #ff5d5d;
      --blue: #78b7ff;
    }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    main {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 24px;
      margin-bottom: 22px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 30px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    p {{ margin: 0; color: var(--muted); }}
    a {{ color: var(--blue); text-decoration: none; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .card, section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    .card span {{ color: var(--muted); font-size: 13px; }}
    .card strong {{ display: block; margin-top: 8px; font-size: 28px; }}
    .layout {{ display: grid; grid-template-columns: 1.25fr 1fr; gap: 14px; margin-bottom: 14px; }}
    section {{ margin-bottom: 14px; }}
    ul {{ margin: 8px 0 0 20px; padding: 0; color: var(--text); }}
    li {{ margin: 6px 0; }}
    table {{ width: 100%; border-collapse: collapse; overflow: hidden; }}
    th, td {{ padding: 10px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ color: var(--muted); font-weight: 700; }}
    tr:last-child td {{ border-bottom: none; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      border: 1px solid var(--muted);
      color: var(--muted);
      padding: 4px 9px;
      font-size: 12px;
      font-weight: 800;
    }}
    .badge.green {{ color: var(--green); border-color: var(--green); }}
    .badge.yellow {{ color: var(--yellow); border-color: var(--yellow); }}
    .badge.red {{ color: var(--red); border-color: var(--red); }}
    .muted {{ color: var(--muted); }}
    .small {{ font-size: 12px; margin-top: 4px; }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }}
    .button {{
      display: inline-flex;
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--text);
      border-radius: 8px;
      padding: 9px 12px;
      font-size: 14px;
    }}
    @media (max-width: 900px) {{
      .grid, .layout {{ grid-template-columns: 1fr; }}
      header {{ align-items: flex-start; flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>AIGILE Delivery Intelligence</h1>
        <p>Morning delivery brief for {escape(str(report.get("project") or "AIGILE"))}. Updated: {escape(str(report.get("created_at") or ""))}</p>
      </div>
      {status_badge(str(report.get("overall_status") or "unknown"))}
    </header>

    <div class="grid">
      <div class="card"><span>Reviewed</span><strong>{escape(str(health.get("reviewed_total") or 0))}</strong></div>
      <div class="card"><span>Red / Yellow / Green</span><strong>{escape(str(counts.get("red") or 0))} / {escape(str(counts.get("yellow") or 0))} / {escape(str(counts.get("green") or 0))}</strong></div>
      <div class="card"><span>No AI Review</span><strong>{escape(str(health.get("unreviewed_total") or 0))}</strong></div>
      <div class="card"><span>Waiting Approval</span><strong>{escape(str(health.get("waiting_human_approval") or 0))}</strong></div>
    </div>

    <div class="layout">
      <section>
        <h2>Morning Brief Summary</h2>
        <p>Mode: {escape(str(brief.get("mode") or "rule_based"))}. AI inference is not required for this fast management brief.</p>
        <ul>{list_items(brief.get("findings") or [])}</ul>
      </section>
      <section>
        <h2>Suggested Actions for Today</h2>
        <ul>{list_items(report.get("suggested_actions") or [])}</ul>
      </section>
    </div>

    <section>
      <h2>Top Risks</h2>
      <table>
        <thead><tr><th>Risk</th><th>Severity</th><th>Source</th><th>Issue</th><th>Suggested action</th></tr></thead>
        <tbody>{''.join(risk_rows)}</tbody>
      </table>
    </section>

    <div class="layout">
      <section>
        <h2>Blockers & Impediments</h2>
        <table>
          <thead><tr><th>Issue</th><th>Title</th><th>Module</th><th>Reason</th></tr></thead>
          <tbody>{issue_rows(report.get("blockers") or [], "No unresolved red issues found.", include_reason=True)}</tbody>
        </table>
      </section>
      <section>
        <h2>Requirement Quality</h2>
        <table>
          <thead><tr><th>Signal</th><th>Count</th></tr></thead>
          <tbody>
            <tr><td>Without acceptance criteria</td><td>{escape(str((rq.get("without_acceptance_criteria") or {}).get("count") or 0))}</td></tr>
            <tr><td>Without type label</td><td>{escape(str((rq.get("without_type_label") or {}).get("count") or 0))}</td></tr>
            <tr><td>Missing info in AI review</td><td>{escape(str((rq.get("missing_info") or {}).get("count") or 0))}</td></tr>
            <tr><td>Yellow/red QA review</td><td>{escape(str((rq.get("yellow_red_qa_review") or {}).get("count") or 0))}</td></tr>
            <tr><td>Risks/dependencies detected</td><td>{escape(str((rq.get("risks_or_dependencies_detected") or {}).get("count") or 0))}</td></tr>
          </tbody>
        </table>
      </section>
    </div>

    <section>
      <h2>Team / Module Signals</h2>
      <table>
        <thead><tr><th>Module</th><th>Red</th><th>Yellow</th><th>Green</th><th>No review</th></tr></thead>
        <tbody>{''.join(module_rows)}</tbody>
      </table>
    </section>

    <section>
      <h2>Decisions Needed</h2>
      <table>
        <thead><tr><th>Decision</th><th>Issue</th><th>Owner</th><th>Recommended action</th></tr></thead>
        <tbody>{''.join(decision_rows)}</tbody>
      </table>
    </section>

    <div class="layout">
      <section>
        <h2>Open Questions</h2>
        <table>
          <thead><tr><th>Issue</th><th>Question</th><th>Module</th><th>Source</th></tr></thead>
          <tbody>{issue_rows(report.get("open_questions") or [], "No open questions captured from task threads.", include_reason=True)}</tbody>
        </table>
      </section>
      <section>
        <h2>Action Items</h2>
        <table>
          <thead><tr><th>Issue</th><th>Action</th><th>Module</th><th>Source</th></tr></thead>
          <tbody>{issue_rows(report.get("action_items") or [], "No action items captured from task threads.", include_reason=True)}</tbody>
        </table>
      </section>
    </div>

    <div class="layout">
      <section>
        <h2>Changes Since Yesterday</h2>
        <p>{escape(str((report.get("changes_since_yesterday") or {}).get("message") or "Not available"))}</p>
      </section>
      <section>
        <h2>Data Sources</h2>
        <table><tbody>{source_rows}</tbody></table>
      </section>
    </div>

    <div class="actions">
      <a class="button" href="/dashboard">Health dashboard</a>
      <a class="button" href="/daily-delivery-brief">Daily brief</a>
      <a class="button" href="/api/delivery-intelligence">JSON report</a>
      <a class="button" href="http://localhost:8080/aigile/projects/882d9973-7e7d-4ad7-ba0f-df2f1c28e825/issues/" target="_blank">Open Plane board</a>
    </div>
  </main>
</body>
</html>"""


def render_health_dashboard(report: dict) -> str:
    rows = []
    for service in report.get("services", []):
        status = service.get("status") or "down"
        status_class = "ok" if service.get("ok") else "down"
        details = service.get("details") or {}
        detail_text = ", ".join(f"{escape(str(key))}: {escape(str(value))}" for key, value in details.items())
        error = service.get("error") or ""
        rows.append(
            f"""
            <tr>
              <td><span class="dot {status_class}"></span>{escape(str(service.get("name") or service.get("id")))}</td>
              <td><span class="badge {status_class}">{escape(str(status).upper())}</span></td>
              <td>{escape(str(service.get("latency_ms") or 0))} ms</td>
              <td><a href="{escape(str(service.get("url") or '#'))}" target="_blank">{escape(str(service.get("url") or ""))}</a></td>
              <td>{detail_text or escape(str(error))}</td>
            </tr>
            """
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AIGILE Health Dashboard</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101214;
      --panel: #171a1f;
      --line: #2b3038;
      --text: #e7e9ee;
      --muted: #a1a8b3;
      --ok: #24c36b;
      --down: #ff5d5d;
    }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    header {{
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 24px;
      margin-bottom: 24px;
    }}
    h1 {{
      font-size: 28px;
      margin: 0 0 8px;
      letter-spacing: 0;
    }}
    p {{
      color: var(--muted);
      margin: 0;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px;
    }}
    .metric strong {{
      display: block;
      font-size: 24px;
      margin-top: 4px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 14px;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
    }}
    tr:last-child td {{
      border-bottom: none;
    }}
    a {{
      color: #78b7ff;
      text-decoration: none;
    }}
    .dot {{
      display: inline-block;
      width: 9px;
      height: 9px;
      margin-right: 10px;
      border-radius: 999px;
      background: var(--down);
    }}
    .dot.ok {{
      background: var(--ok);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      border: 1px solid var(--down);
      color: var(--down);
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 700;
    }}
    .badge.ok {{
      border-color: var(--ok);
      color: var(--ok);
    }}
    .actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 18px;
    }}
    .button {{
      border: 1px solid var(--line);
      color: var(--text);
      background: var(--panel);
      border-radius: 8px;
      padding: 9px 12px;
    }}
    @media (max-width: 760px) {{
      header, .summary {{
        display: block;
      }}
      .metric {{
        margin-bottom: 10px;
      }}
      table, tbody, tr, td, th {{
        display: block;
      }}
      thead {{
        display: none;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>AIGILE Health Dashboard</h1>
        <p>Local runtime status. Updated: {escape(str(report.get("created_at")))}</p>
      </div>
      <span class="badge {'ok' if report.get('ok') else 'down'}">{escape(str(report.get("status", "unknown")).upper())}</span>
    </header>
    <section class="summary">
      <div class="metric">Services<strong>{escape(str(report.get("services_total")))}</strong></div>
      <div class="metric">OK<strong>{escape(str(report.get("services_ok")))}</strong></div>
      <div class="metric">Down / Warn<strong>{escape(str(report.get("services_down")))}</strong></div>
    </section>
    <table>
      <thead>
        <tr>
          <th>Service</th>
          <th>Status</th>
          <th>Latency</th>
          <th>Open</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    <div class="actions">
      <a class="button" href="/healthz">JSON health</a>
      <a class="button" href="/api/review-history">AI review history API</a>
    </div>
  </main>
</body>
</html>"""


def issue_to_payload(issue: Issue) -> dict:
    issue_type = issue.type.name if issue.type else "Task"
    labels = list(issue.labels.filter(deleted_at__isnull=True).values_list("name", flat=True))
    return {
        "id": str(issue.id),
        "key": f"{issue.project.identifier}-{issue.sequence_id}",
        "title": issue.name,
        "description": issue.description_stripped or strip_tags(issue.description_html or "") or "",
        "priority": issue.priority,
        "state": issue.state.name if issue.state else "",
        "state_group": issue.state.group if issue.state else "",
        "type": issue_type,
        "labels": labels,
        "project": {
            "id": str(issue.project.id),
            "name": issue.project.name,
            "identifier": issue.project.identifier,
        },
        "workspace": {
            "id": str(issue.workspace.id),
            "name": issue.workspace.name,
            "slug": issue.workspace.slug,
        },
        "url": f"http://localhost:8080/{issue.workspace.slug}/browse/{issue.project.identifier}-{issue.sequence_id}",
    }


def compact_issue_payload(issue: Issue | None, description_limit: int = 1200) -> dict | None:
    if not issue:
        return None
    payload = issue_to_payload(issue)
    description = payload.get("description") or ""
    if len(description) > description_limit:
        payload["description"] = description[:description_limit].rstrip() + "..."
    return payload


def build_parent_chain(issue: Issue, limit: int = 5) -> list[dict]:
    chain = []
    parent = issue.parent
    depth = 0
    while parent and depth < limit:
        chain.append(compact_issue_payload(parent))
        parent = parent.parent
        depth += 1
    return chain


def build_issue_context_graph(issue: Issue) -> dict:
    issue_key = f"{issue.project.identifier}-{issue.sequence_id}"
    children = Issue.objects.select_related("workspace", "project", "state", "type").prefetch_related("labels").filter(
        parent=issue,
        deleted_at__isnull=True,
    ).order_by("sequence_id")[:20]
    outgoing = IssueRelation.objects.select_related(
        "related_issue",
        "related_issue__workspace",
        "related_issue__project",
        "related_issue__state",
        "related_issue__type",
    ).prefetch_related("related_issue__labels").filter(issue=issue, deleted_at__isnull=True)
    incoming = IssueRelation.objects.select_related(
        "issue",
        "issue__workspace",
        "issue__project",
        "issue__state",
        "issue__type",
    ).prefetch_related("issue__labels").filter(related_issue=issue, deleted_at__isnull=True)
    cycle_links = CycleIssue.objects.select_related("cycle").filter(issue=issue, deleted_at__isnull=True)
    module_links = ModuleIssue.objects.select_related("module").filter(issue=issue, deleted_at__isnull=True)
    latest_review = latest_review_for_issue(issue_key)
    return {
        "issue_key": issue_key,
        "current": compact_issue_payload(issue, description_limit=3000),
        "parents": [item for item in build_parent_chain(issue) if item],
        "children": [compact_issue_payload(child, description_limit=700) for child in children],
        "relations": {
            "outgoing": [
                {
                    "relation_type": relation.relation_type,
                    "issue": compact_issue_payload(relation.related_issue, description_limit=700),
                }
                for relation in outgoing
            ],
            "incoming": [
                {
                    "relation_type": relation.relation_type,
                    "issue": compact_issue_payload(relation.issue, description_limit=700),
                }
                for relation in incoming
            ],
        },
        "cycles": [
            {
                "id": str(link.cycle.id),
                "name": link.cycle.name,
                "description": link.cycle.description or "",
                "start_date": link.cycle.start_date.isoformat() if link.cycle.start_date else None,
                "end_date": link.cycle.end_date.isoformat() if link.cycle.end_date else None,
            }
            for link in cycle_links
        ],
        "modules": [
            {
                "id": str(link.module.id),
                "name": link.module.name,
                "description": link.module.description or "",
                "status": link.module.status,
                "start_date": link.module.start_date.isoformat() if link.module.start_date else None,
                "target_date": link.module.target_date.isoformat() if link.module.target_date else None,
            }
            for link in module_links
        ],
        "latest_review": latest_review,
    }


def find_issue(payload: dict) -> Issue:
    issue_id = payload.get("issue_id") or payload.get("id")
    issue_key = payload.get("issue_key") or payload.get("key")
    workspace_slug = payload.get("workspace_slug") or payload.get("workspace") or "aigile"

    qs = Issue.objects.select_related("workspace", "project", "state", "type").prefetch_related("labels")
    qs = qs.filter(deleted_at__isnull=True)

    if issue_id:
        return qs.get(id=issue_id)

    if issue_key:
        match = re.match(r"^([A-Za-z0-9_]+)-(\d+)$", str(issue_key).strip())
        if not match:
            raise ValueError("issue_key must look like AIGILE-123")
        identifier, sequence_id = match.group(1).upper(), int(match.group(2))
        return qs.get(workspace__slug=workspace_slug, project__identifier=identifier, sequence_id=sequence_id)

    project_id = payload.get("project_id")
    sequence_id = payload.get("sequence_id")
    if project_id and sequence_id:
        return qs.get(project_id=project_id, sequence_id=int(sequence_id))

    raise ValueError("Missing issue_id or issue_key")


def refresh_knowledge_base() -> str:
    close_old_connections()
    ensure_dirs()
    ws = Workspace.objects.filter(slug="aigile").first()
    lines = [
        "# AIGILE Knowledge Base Snapshot",
        f"Generated: {utc_now_iso()}",
        "",
        "AIGILE is an AI-native Delivery Operating System.",
        "Plane is the source of truth for work items. Mattermost is the notification layer.",
        "Manual trigger must use this latest local snapshot without waiting for the daily refresh.",
        "",
        "## Projects",
    ]
    if ws:
        for project in Project.objects.filter(workspace=ws, deleted_at__isnull=True).order_by("name"):
            lines.append(f"- {project.name} ({project.identifier}): {project.description or ''}")
    lines.extend(["", "## Labels"])
    if ws:
        labels = Label.objects.filter(workspace=ws, deleted_at__isnull=True).order_by("name").values_list("name", flat=True)
        lines.extend([f"- {label}" for label in labels])
    lines.extend(["", "## Views"])
    if ws:
        views = IssueView.objects.filter(workspace=ws, deleted_at__isnull=True, archived_at__isnull=True).order_by("name")
        lines.extend([f"- {view.name}: {view.description or ''}" for view in views])
    KB_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_execution_log({"event": "knowledge_base_refresh", "status": "success", "path": str(KB_PATH)})
    return str(KB_PATH)


def read_knowledge_base() -> str:
    if not KB_PATH.exists():
        refresh_knowledge_base()
    return KB_PATH.read_text(encoding="utf-8")[:12000]


def ollama_model_candidates() -> list[str]:
    configured = [OLLAMA_MODEL] + [model for model in OLLAMA_FALLBACK_MODELS if model != OLLAMA_MODEL]
    installed = []
    try:
        req = Request(f"{OLLAMA_BASE_URL}/api/tags", method="GET")
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        for item in data.get("models") or []:
            name = item.get("name") or item.get("model")
            if name and "embed" not in name.lower() and "30b" not in name.lower():
                installed.append(name)
    except Exception as exc:
        logger.warning("Could not read Ollama model list: %s", exc)

    candidates = [model for model in configured if model in installed]
    candidates.extend(model for model in installed if model not in candidates)
    candidates.extend(model for model in configured if model not in candidates)
    return candidates


def ollama_chat_completion(messages: list[dict], options: dict | None = None, timeout: int = 180) -> dict:
    last_error = None
    for model in ollama_model_candidates():
        body = {
            "model": model,
            "stream": False,
            "messages": messages,
            "options": options or {"temperature": 0.2},
        }
        req = Request(
            f"{OLLAMA_BASE_URL}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
            if model != OLLAMA_MODEL:
                logger.info("Used Ollama fallback model %s because configured model is unavailable", model)
            return raw
        except Exception as exc:
            last_error = exc
            logger.warning("Ollama model %s failed: %s", model, exc)
    raise RuntimeError(f"Ollama chat failed for all local models: {last_error}")


def post_json(url: str, payload: dict, timeout: int = 90) -> dict:
    req = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def rag_post(path: str, payload: dict, timeout: int = 90) -> dict:
    return post_json(f"{RAG_BACKEND_URL}{path}", payload, timeout=timeout)


def format_rag_context(matches: list[dict], title: str) -> str:
    if not matches:
        return ""
    parts = [f"## {title}", "These Plane Pages rules are strict project knowledge. If they conflict with generic model behavior, follow Plane Pages."]
    for index, match in enumerate(matches, start=1):
        metadata = match.get("metadata") or {}
        parts.append(
            f"\n[{index}] {match.get('title') or metadata.get('title')} ({match.get('source_path') or metadata.get('source_path')})\n"
            f"{str(match.get('text') or '')[:1500]}"
        )
    return "\n".join(parts)


def read_project_pages_context(query: str, limit: int = 5) -> str:
    try:
        result = rag_post(
            "/rag/query",
            {"collection": PLANE_PAGES_COLLECTION, "query": query, "limit": limit, "search_limit": 20},
            timeout=60,
        )
        return format_rag_context(result.get("matches") or [], "Plane Pages Project Knowledge")
    except Exception as exc:
        logger.warning("Plane Pages RAG context unavailable: %s", exc)
        return ""


def build_review_project_pages_query(issue_payload: dict, detected_type: str, agent_names: list[str]) -> str:
    return "\n".join([
        "AIGILE Agent Rules",
        "Agent Response Rules",
        f"{detected_type} Template",
        "AI Review Gate",
        f"Task type: {detected_type}",
        "Agents: " + ", ".join(agent_names),
        issue_payload.get("key", ""),
        issue_payload.get("title", ""),
        issue_payload.get("description", ""),
        "Labels: " + " ".join(issue_payload.get("labels") or []),
    ])


def build_review_context(issue_payload: dict, detected_type: str, agent_names: list[str]) -> str:
    pages_query = build_review_project_pages_query(issue_payload, detected_type, agent_names)
    return "\n\n".join(
        item
        for item in [read_knowledge_base(), read_project_pages_context(pages_query, limit=8)]
        if item
    )


def build_task_chat_project_pages_query(issue: dict, user_message: str, thread_history: str) -> str:
    latest_review = issue.get("latest_review") if isinstance(issue.get("latest_review"), dict) else {}
    issue_type = issue.get("type") or issue.get("detected_type") or latest_review.get("detected_type") or ""
    return "\n".join([
        "AIGILE Agent Rules",
        "Agent Response Rules",
        f"{issue_type} Template",
        "Task Chat Agent",
        "Mattermost task thread",
        "Acceptance Criteria",
        str(issue.get("key") or ""),
        str(issue.get("title") or ""),
        user_message,
        thread_history[-2000:],
    ])


def next_refresh_delay() -> float:
    now = datetime.now()
    target = now.replace(hour=REFRESH_HOUR, minute=REFRESH_MINUTE, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def scheduler_loop() -> None:
    while True:
        try:
            time.sleep(next_refresh_delay())
            refresh_knowledge_base()
            sync_plane_pages_to_rag({"scheduled": True})
        except Exception as exc:
            logger.exception("Scheduled KB refresh failed")
            append_execution_log({"event": "knowledge_base_refresh", "status": "failure", "error": str(exc)})
            time.sleep(60)


def initial_refresh_loop() -> None:
    try:
        refresh_knowledge_base()
    except Exception as exc:
        logger.exception("Initial KB refresh failed")
        append_execution_log({"event": "knowledge_base_refresh", "status": "failure", "error": str(exc), "phase": "startup"})


def ollama_chat(issue: dict, context: str) -> dict:
    prompt = f"""
You are AI Delivery Assistant for AIGILE, an AI-native Delivery Operating System.
Use the latest local knowledge base context and analyze this Plane work item.

Return STRICT JSON with keys:
preview_summary, full_analysis, risks, dependencies, acceptance_criteria,
implementation_plan, codex_prompt, status.

Keep preview_summary short for Mattermost. Make codex_prompt directly usable.

Knowledge base:
{context}

Issue:
{json.dumps(issue, ensure_ascii=False, indent=2)}
"""
    raw = ollama_chat_completion(
        [
            {"role": "system", "content": "You are a concise enterprise delivery AI assistant. Return only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        {"temperature": 0.2},
    )
    content = raw.get("message", {}).get("content", "{}").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {
            "preview_summary": content[:500],
            "full_analysis": content,
            "risks": [],
            "dependencies": [],
            "acceptance_criteria": [],
            "implementation_plan": [],
            "codex_prompt": "",
            "status": "analysis_ready",
        }
    return parsed


def as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [str(value)]


def canonical_issue_type(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[_\s]+", " ", str(value).strip().lower()).replace("tech debt", "tech-debt")
    normalized = re.sub(r"^(type|тип)\s*:\s*", "", normalized)
    return KNOWN_ISSUE_TYPES.get(normalized) or KNOWN_ISSUE_TYPES.get(normalized.replace("-", " "))


def detect_issue_type(issue: dict) -> str:
    for label in issue.get("labels") or []:
        detected = canonical_issue_type(label)
        if detected:
            return detected

    direct = canonical_issue_type(issue.get("type"))
    if direct and direct != "Task":
        return direct

    title = issue.get("title") or ""
    prefix = re.match(
        r"^\[([^\]]+)\]|^(bug|баг|ошибка|дефект|story|история|epic|эпик|research|исследование|release|релиз|tech debt|tech-debt|техдолг|технический долг|task|задача)\s*:",
        title.strip(),
        re.IGNORECASE,
    )
    if prefix:
        detected = canonical_issue_type(prefix.group(1) or prefix.group(2))
        if detected:
            return detected

    return "Task"


def agents_for_issue_type(issue_type: str) -> list[str]:
    return AGENT_MAP.get(issue_type, AGENT_MAP["Task"])


def overall_review_status(agents: list[dict]) -> str:
    statuses = [str(agent.get("status") or "").lower() for agent in agents]
    if "red" in statuses:
        return "red"
    if "yellow" in statuses:
        return "yellow"
    return "green"


def has_meaningful_text(value: str | None) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return bool(text)


def has_acceptance_signal(value: str | None) -> bool:
    text = str(value or "").lower()
    signals = [
        "acceptance",
        "criteria",
        "критер",
        "приемк",
        "приёмк",
        "ожидаем",
        "готово",
        "done",
    ]
    return any(signal in text for signal in signals)


def has_type_label(issue: dict) -> bool:
    return any(canonical_issue_type(label) for label in issue.get("labels") or [])


def deterministic_gate_review(issue_type: str, issue: dict) -> dict | None:
    findings = []
    description = issue.get("description")

    if not has_meaningful_text(issue.get("title")):
        findings.append(
            {
                "severity": "high",
                "title": "Missing title",
                "description": "Work item has no clear title.",
                "recommendation": "Add a short title that explains the user-visible problem or delivery goal.",
                "can_be_applied": False,
            }
        )

    if not has_meaningful_text(description):
        findings.append(
            {
                "severity": "high",
                "title": "Description is missing",
                "description": "Only the title is filled in. The task cannot be reliably implemented, reviewed, or tested from the current data.",
                "recommendation": "Add context, expected behavior, current behavior, constraints, and the intended result before sending the task to delivery.",
                "can_be_applied": False,
            }
        )
    elif issue_type in {"Story", "Task", "Bug", "Epic", "Tech Debt", "Release"} and not has_acceptance_signal(description):
        findings.append(
            {
                "severity": "medium",
                "title": "Acceptance signal is missing",
                "description": "The description does not contain clear acceptance criteria or an equivalent expected-result section.",
                "recommendation": "Add 3-5 verifiable acceptance criteria or expected results.",
                "can_be_applied": True,
            }
        )

    if not findings:
        return None

    status = "red" if any(item["severity"] == "high" for item in findings) else "yellow"
    return {
        "agent_name": "AIGILE Review Gate",
        "status": status,
        "summary": "Задача не готова к delivery." if status == "red" else "Задачу лучше уточнить перед delivery.",
        "findings": [normalize_finding(item) for item in findings],
        "proposed_task_patch": default_patch(),
    }


def default_patch() -> dict:
    return {
        "title": "",
        "description": "",
        "acceptance_criteria": [],
        "test_cases": [],
        "risks": [],
        "dependencies": [],
    }


def as_clean_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def is_blocking_suggestion(agent: dict, finding: dict | None) -> bool:
    if str(agent.get("status") or "").lower() == "red":
        return True
    return bool(finding and str(finding.get("severity") or "").lower() == "high")


def red_fill_template(issue_type: str) -> list[str]:
    templates = {
        "Bug": [
            "### Шаблон для заполнения бага",
            "",
            "Заполните этот блок вручную и запустите AI анализ повторно.",
            "",
            "#### Текущее поведение",
            "",
            "Что сейчас происходит:",
            "",
            "#### Ожидаемое поведение",
            "",
            "Что должно происходить:",
            "",
            "#### Шаги воспроизведения",
            "",
            "- 1.",
            "- 2.",
            "- 3.",
            "",
            "#### Окружение",
            "",
            "- Стенд: local / staging / production",
            "- ОС:",
            "- Браузер:",
            "- Версия приложения / commit:",
            "",
            "#### Влияние",
            "",
            "Кого затрагивает и насколько критично:",
            "",
            "#### Критерии приемки",
            "",
            "- [ ] Баг воспроизводится по описанным шагам.",
            "- [ ] Причина найдена и описана.",
            "- [ ] Исправление реализовано.",
            "- [ ] Добавлена проверка или regression test.",
            "- [ ] Исправление проверено на нужном окружении.",
        ],
        "Story": [
            "### Шаблон для заполнения Story",
            "",
            "Заполните этот блок вручную и запустите AI анализ повторно.",
            "",
            "#### Пользовательская ценность",
            "",
            "Кому и зачем нужна эта история:",
            "",
            "#### User story",
            "",
            "Как [роль], я хочу [действие], чтобы [ценность].",
            "",
            "#### Acceptance criteria",
            "",
            "- [ ]",
            "- [ ]",
            "- [ ]",
            "",
            "#### Ограничения и зависимости",
            "",
            "-",
            "",
            "#### Негативные сценарии",
            "",
            "-",
        ],
        "Task": [
            "### Шаблон для заполнения Task",
            "",
            "Заполните этот блок вручную и запустите AI анализ повторно.",
            "",
            "#### Цель",
            "",
            "Что нужно сделать и зачем:",
            "",
            "#### Контекст",
            "",
            "Что уже известно:",
            "",
            "#### Что изменить",
            "",
            "-",
            "",
            "#### Критерии готовности",
            "",
            "- [ ]",
            "- [ ]",
            "- [ ]",
            "",
            "#### Проверка",
            "",
            "Как убедиться, что задача выполнена:",
        ],
        "Epic": [
            "### Шаблон для заполнения Epic",
            "",
            "Заполните этот блок вручную и запустите AI анализ повторно.",
            "",
            "#### Цель эпика",
            "",
            "Какую продуктовую цель закрывает эпик:",
            "",
            "#### Scope",
            "",
            "Что входит:",
            "",
            "#### Out of scope",
            "",
            "Что не входит:",
            "",
            "#### Основные фичи / задачи",
            "",
            "-",
            "",
            "#### Риски и зависимости",
            "",
            "-",
            "",
            "#### Definition of Done",
            "",
            "- [ ]",
        ],
    }
    return templates.get(issue_type, templates["Task"])


def format_ai_comment_markdown(agent: dict, finding: dict | None, issue_type: str = "Task") -> str:
    patch = agent.get("proposed_task_patch") if isinstance(agent.get("proposed_task_patch"), dict) else {}
    lines = [
        "## AI замечание по задаче",
        "",
        "AI не менял описание задачи. Ниже — рекомендация для ручной проверки и заполнения.",
        "",
        f"- Агент: {agent.get('agent_name') or 'AI Agent'}",
    ]
    if finding:
        lines.extend(
            [
                "",
                "### Было",
                "",
                finding.get("description") or finding.get("title") or "В задаче не хватает данных для уверенной передачи в работу.",
                "",
                "### Почему это риск",
                "",
                risk_text_for_finding(finding),
                "",
                "### Стало / что нужно добавить",
                "",
                finding.get("recommendation") or "Добавить недостающий контекст и запустить AI анализ повторно.",
            ]
        )
    if is_blocking_suggestion(agent, finding):
        lines.extend(["", "### Нужно заполнить перед delivery", ""])
        lines.extend(red_fill_template(issue_type))
    if agent.get("summary"):
        lines.extend(["", "### Резюме агента", "", str(agent["summary"])])

    description = str(patch.get("description") or "").strip()
    if description:
        lines.extend(["", "### Предложенное описание", "", description])

    sections = [
        ("Критерии приемки", patch.get("acceptance_criteria")),
        ("Тест-кейсы", patch.get("test_cases")),
        ("Риски", patch.get("risks")),
        ("Зависимости", patch.get("dependencies")),
    ]
    for title, items in sections:
        clean_items = as_clean_list(items)
        if clean_items:
            lines.extend(["", f"### {title}", ""])
            lines.extend([f"- {item}" for item in clean_items])

    if finding and finding.get("description"):
        lines.extend(["", "### Детали замечания", "", str(finding["description"])])

    return "\n".join(lines).strip() + "\n"


def risk_text_for_finding(finding: dict) -> str:
    severity = str(finding.get("severity") or "").lower()
    if severity == "high":
        return "Задачу нельзя надежно реализовать, проверить или передать в работу без уточнения."
    if severity == "medium":
        return "Задачу можно обсуждать дальше, но есть риск неверной реализации или неполной проверки."
    return "Замечание не блокирует работу, но поможет сделать задачу понятнее."


def markdown_to_basic_html(markdown: str) -> str:
    html_lines = []
    in_list = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue
        if line.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{escape(line[4:])}</h3>")
        elif line.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{escape(line[3:])}</h2>")
        elif line.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{escape(line[2:])}</li>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{escape(line)}</p>")
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def text_to_description_json(text: str) -> dict:
    content = []
    for line in text.splitlines():
        if line.strip():
            content.append({"type": "paragraph", "content": [{"type": "text", "text": line.strip()}]})
        else:
            content.append({"type": "paragraph"})
    return {"type": "doc", "content": content or [{"type": "paragraph"}]}


def ensure_ai_label(issue: Issue, name: str, description: str, color: str) -> Label:
    label = Label.objects.filter(workspace=issue.workspace, project=issue.project, name=name, deleted_at__isnull=True).first()
    if not label:
        label = Label.objects.create(
            workspace=issue.workspace,
            project=issue.project,
            name=name,
            description=description,
            color=color,
        )
    return label


def ensure_ai_reviewed_label(issue: Issue) -> Label:
    return ensure_ai_label(issue, "AI-R", "AI reviewed this task.", "#2563EB")


def ensure_ai_assisted_label(issue: Issue) -> Label:
    return ensure_ai_label(issue, "AI-A", "AI added an assistance comment to this task.", "#8B5CF6")


def ensure_ai_agent_assisted_label(issue: Issue) -> Label:
    return ensure_ai_label(issue, "AIA", "AI agent updated this task after approval.", "#06B6D4")


def add_issue_label(issue: Issue, label: Label) -> None:
    if not IssueLabel.objects.filter(workspace=issue.workspace, project=issue.project, issue=issue, label=label, deleted_at__isnull=True).exists():
        IssueLabel.objects.create(
            workspace=issue.workspace,
            project=issue.project,
            issue=issue,
            label=label,
        )
    if hasattr(issue, "updated_at"):
        issue.updated_at = datetime.now(timezone.utc)
        issue.save(update_fields=["updated_at"])


def create_issue_comment(issue: Issue, markdown: str) -> IssueComment:
    actor = getattr(issue, "updated_by", None) or getattr(issue, "created_by", None)
    return IssueComment.objects.create(
        workspace=issue.workspace,
        project=issue.project,
        issue=issue,
        actor=actor,
        created_by=actor,
        updated_by=actor,
        comment_html=markdown_to_basic_html(markdown),
        comment_stripped=markdown,
        comment_json=text_to_description_json(markdown),
        access="INTERNAL",
    )


DEMO_ISSUES = [
    {
        "key": "story-delivery-slot",
        "type_label": "Story",
        "state": "Discovery",
        "priority": "medium",
        "title": "[DEMO] Story - Customer can request delivery slot",
        "description": """## User Story
As a customer, I want to request a preferred delivery slot so that I can plan when my order arrives.

## Current Scope
- Customer selects a delivery date.
- Customer submits the request during checkout.
- Operations team should see the requested slot.

## Known Gaps For Demo
- Acceptance criteria are intentionally incomplete.
- Edge cases are not defined.
- Validation rules for unavailable slots are missing.
- Timezone and availability risks are not resolved.

## Acceptance Criteria
- Customer can choose a delivery date.
""",
    },
    {
        "key": "bug-payment-confirmation",
        "type_label": "Bug",
        "state": "Triage",
        "priority": "high",
        "title": "[DEMO] Bug - Payment confirmation is not shown after successful payment",
        "description": """## Problem
Some customers say they are not sure whether payment succeeded.

## Context
The payment provider returns success, but the user may not see a clear confirmation screen.

## Known Gaps For Demo
- Actual result is missing.
- Expected result is missing.
- Steps to reproduce are missing.
- Browser / device / environment are missing.
- Impact is described only vaguely.
""",
    },
    {
        "key": "epic-ai-backlog-refinement",
        "type_label": "Epic",
        "state": "Discovery",
        "priority": "high",
        "title": "[DEMO] Epic - AI-assisted backlog refinement",
        "description": """## Product Idea
Use AI agents to help Product and Delivery Managers refine backlog items before they are handed to engineering.

## Business Outcome
Improve task readiness and reduce clarification loops during sprint planning.

## Initial Scope
- AI reviews work items.
- AI suggests missing acceptance criteria, risks, and dependencies.
- Human approval remains mandatory.

## Known Gaps For Demo
- Decomposition into features and stories is incomplete.
- Dependency mapping is not complete.
- Rollout strategy is missing.
- Security and audit requirements are not finalized.
- Success metrics are still broad.
""",
    },
    {
        "key": "techdebt-review-orchestration",
        "type_label": "Tech Debt",
        "state": "Refinement",
        "priority": "medium",
        "title": "[DEMO] Tech Debt - Refactor AI review orchestration",
        "description": """## Technical Context
AI Review Gate currently orchestrates several review agents from the AIGILE backend.

## Proposed Direction
Refactor orchestration so agent routing, prompt building, and result normalization are easier to extend.

## Trade-offs
- Cleaner extension path for new agents.
- Risk of breaking the current working review flow.
- May require new tests around retries and partial failures.

## Known Gaps For Demo
- Rollback plan is missing.
- Performance constraints are missing.
- Migration steps are missing.
- Compatibility with current Plane and Mattermost flows needs review.
""",
    },
]


def find_demo_project() -> Project:
    workspace = Workspace.objects.filter(slug=PLANE_PAGES_WORKSPACE_SLUG, deleted_at__isnull=True).first()
    if not workspace:
        raise ValueError(f"Workspace not found: {PLANE_PAGES_WORKSPACE_SLUG}")
    project = Project.objects.filter(
        workspace=workspace,
        identifier=PLANE_PAGES_PROJECT_IDENTIFIER,
        deleted_at__isnull=True,
    ).first()
    if not project:
        raise ValueError(f"Project not found: {PLANE_PAGES_PROJECT_IDENTIFIER}")
    return project


def ensure_project_label(project: Project, name: str, description: str, color: str) -> Label:
    label = Label.objects.filter(workspace=project.workspace, project=project, name=name, deleted_at__isnull=True).first()
    if not label:
        label = Label.objects.create(
            workspace=project.workspace,
            project=project,
            name=name,
            description=description,
            color=color,
            created_by=getattr(project, "created_by", None),
            updated_by=getattr(project, "updated_by", None) or getattr(project, "created_by", None),
        )
    return label


def default_demo_state(project: Project, state_name: str | None = None) -> State | None:
    if state_name:
        state = State.objects.filter(
            workspace=project.workspace,
            project=project,
            name__iexact=state_name,
            deleted_at__isnull=True,
        ).first()
        if state:
            return state
    return (
        State.objects.filter(workspace=project.workspace, project=project, default=True, deleted_at__isnull=True).first()
        or State.objects.filter(workspace=project.workspace, project=project, name__iexact="Backlog", deleted_at__isnull=True).first()
        or State.objects.filter(workspace=project.workspace, project=project, deleted_at__isnull=True).order_by("sequence").first()
    )


def next_issue_sequence(project: Project) -> int:
    last = Issue.objects.filter(workspace=project.workspace, project=project).order_by("-sequence_id").values_list("sequence_id", flat=True).first()
    return int(last or 0) + 1


def reset_issue_labels(issue: Issue, labels: list[Label]) -> None:
    IssueLabel.objects.filter(workspace=issue.workspace, project=issue.project, issue=issue, deleted_at__isnull=True).exclude(
        label__in=labels
    ).update(deleted_at=datetime.now(timezone.utc))
    for label in labels:
        add_issue_label(issue, label)


def upsert_demo_issue(project: Project, spec: dict) -> Issue:
    demo_label = ensure_project_label(project, DEMO_LABEL_NAME, "AIGILE live demo work item.", "#F59E0B")
    type_label = ensure_project_label(project, spec["type_label"], f"Demo type label: {spec['type_label']}.", "#22C55E")
    state = default_demo_state(project, spec.get("state"))
    title = spec["title"]
    actor = getattr(project, "updated_by", None) or getattr(project, "created_by", None)
    issue = (
        Issue.objects.filter(workspace=project.workspace, project=project, name=title, deleted_at__isnull=True).first()
        or Issue.objects.filter(workspace=project.workspace, project=project, external_source="aigile-demo", external_id=spec["key"], deleted_at__isnull=True).first()
    )
    created = False
    if not issue:
        issue = Issue(
            workspace=project.workspace,
            project=project,
            name=title,
            sequence_id=next_issue_sequence(project),
            sort_order=65535,
            created_by=actor,
            updated_by=actor,
            external_source="aigile-demo",
            external_id=spec["key"],
        )
        created = True
    issue.name = title
    issue.description_stripped = spec["description"]
    issue.description_html = markdown_to_basic_html(spec["description"])
    issue.description_json = text_to_description_json(spec["description"])
    issue.state = state
    issue.priority = spec.get("priority", "medium")
    issue.is_draft = False
    issue.archived_at = None
    issue.completed_at = None
    issue.external_source = "aigile-demo"
    issue.external_id = spec["key"]
    issue.updated_by = actor
    issue.save()
    if created:
        IssueSequence.objects.create(
            workspace=project.workspace,
            project=project,
            issue=issue,
            sequence=issue.sequence_id,
            created_by=actor,
            updated_by=actor,
        )
    reset_issue_labels(issue, [demo_label, type_label])
    return issue


def seed_demo_data(reset: bool = False) -> dict:
    project = find_demo_project()
    with transaction.atomic():
        issues = [upsert_demo_issue(project, spec) for spec in DEMO_ISSUES]
    action = "reset" if reset else "seed"
    result = {
        "ok": True,
        "action": action,
        "project": project.name,
        "project_identifier": project.identifier,
        "label": DEMO_LABEL_NAME,
        "count": len(issues),
        "issues": [
            {
                "key": f"{project.identifier}-{issue.sequence_id}",
                "title": issue.name,
                "url": issue_to_payload(issue)["url"],
            }
            for issue in issues
        ],
    }
    append_execution_log({"event": f"demo_{action}", "count": len(issues)})
    return result


def mark_issue_with_ai_label(issue: Issue, label_kind: str) -> str | None:
    if not getattr(issue, "workspace", None) or not getattr(issue, "project", None):
        return None
    try:
        if label_kind == "reviewed":
            label = ensure_ai_reviewed_label(issue)
        elif label_kind == "assisted":
            label = ensure_ai_assisted_label(issue)
        elif label_kind == "agent_assisted":
            label = ensure_ai_agent_assisted_label(issue)
        else:
            return None
        add_issue_label(issue, label)
        return label.name
    except Exception:
        logger.exception("Failed to add AI label %s", label_kind)
        return None


AGENT_RULES_PAGE_TITLE = "[AI] AIGILE Agent Rules"
AGENT_RULES_PAGE_MARKDOWN = """# AIGILE Agent Rules

This page is a strict project knowledge source for AIGILE agents. It is approved for RAG because the title contains [AI] and the page is Public.

## Global Rules

- Answer in English by default while the demo language mode is English.
- Be concise by default.
- Use Plane Pages project knowledge as strict rules.
- Do not update Plane without explicit user approval.
- If context is insufficient, say what is missing.
- Keep Mattermost messages readable and avoid long technical dumps.

## AI Review Gate

- Use green only when the task is clear for delivery.
- Use yellow when the task is usable but would benefit from clarification.
- Use red when critical context, acceptance criteria, security, QA, architecture, or delivery information is missing.
- Recommendations must be practical and tied to the task.

## Bug Agent

- Require current behavior, expected behavior, reproduction steps, environment, severity, and acceptance criteria.
- If a bug lacks reproduction steps, mark at least yellow.
- If a bug cannot be tested from the description, mark red.

## Story Agent

- Require user value, acceptance criteria, dependencies, and edge cases.
- Suggest testable acceptance criteria.
- Keep implementation advice separate from product value.

## Task Chat Agent

- Treat the Mattermost thread as task-scoped memory.
- When the user asks to add acceptance criteria, create a draft and wait for y/да.
- Approved acceptance criteria update the Plane description block `Acceptance Criteria`, add `[AI]` to the new line, add label `AIA`, and leave a short summary comment.
- Other approved updates go to comments unless a narrower approved field-update flow exists.

## Comment Format

- Keep Plane comments short.
- Include: Пользователь, Запрос, Изменение.
- Do not paste large internal prompts or stack traces.
"""


PLANE_KNOWLEDGE_TEMPLATE_PAGES = {
    AGENT_RULES_PAGE_TITLE: AGENT_RULES_PAGE_MARKDOWN,
    "[AI] Bug Template": """# Bug Template

This page is strict project knowledge for Bug work items in AIGILE.

## Required Fields

- Summary: short user-visible problem.
- Current behavior: what happens now.
- Expected behavior: what should happen.
- Steps to reproduce: numbered, reproducible path.
- Environment: browser, OS, service, deployment, data state.
- Severity: business or user impact.
- Priority: delivery priority.
- Regression: yes/no/unknown.
- Root cause: known/unknown.
- Acceptance Criteria: testable fix conditions.

## AI Review Rules

- If reproduction steps are missing, status should be at least yellow.
- If current and expected behavior are both missing, status should be red.
- If the bug cannot be tested from the description, status should be red.
- If environment is unknown but the bug is still understandable, status can be yellow.
- Do not invent root cause. Mark it as unknown when needed.

## Recommended Plane Description

```text
## Problem

## Current Behavior

## Expected Behavior

## Steps To Reproduce
1.
2.
3.

## Environment

## Severity / Priority

## Regression

## Acceptance Criteria
- 
```
""",
    "[AI] Story Template": """# Story Template

This page is strict project knowledge for Story work items in AIGILE.

## Required Fields

- User / actor.
- User goal.
- Product value.
- Scope.
- Out of scope.
- Acceptance Criteria.
- Dependencies.
- UX notes when relevant.
- Analytics or KPI when relevant.
- Edge cases.

## AI Review Rules

- If user value is missing, Product Owner Agent should mark yellow or red.
- If Acceptance Criteria are missing, QA Agent should mark yellow or red.
- If dependencies are unknown, Architect Agent should call this out.
- Keep implementation notes separate from product behavior.

## Recommended Plane Description

```text
## User Story
As a ...
I want ...
So that ...

## Scope

## Out Of Scope

## Acceptance Criteria
-

## Dependencies

## UX / Analytics Notes

## Edge Cases
```
""",
    "[AI] Epic Template": """# Epic Template

This page is strict project knowledge for Epic work items in AIGILE.

## Required Fields

- Product goal.
- Business outcome.
- Target users.
- Scope boundaries.
- Child features / stories.
- Key risks.
- Dependencies.
- Release assumptions.
- Success metrics.

## AI Review Rules

- If business outcome is missing, Product Manager Agent should mark yellow or red.
- If architecture or integration boundaries are unclear, Architect Agent should mark yellow.
- If release assumptions are missing, Delivery Manager Agent should mark yellow.
- If security impact is unknown for auth, data, or access features, Security Engineer Agent should call it out.

## Recommended Plane Description

```text
## Product Goal

## Business Outcome

## Scope

## Out Of Scope

## Child Work Items

## Risks

## Dependencies

## Success Metrics

## Release Notes
```
""",
    "[AI] Agent Response Rules": """# Agent Response Rules

This page is strict project knowledge for how AIGILE agents should respond in Plane and Mattermost.

## General Style

- Answer in English by default while the demo language mode is English.
- Be concise first, detailed only when asked.
- Do not expose stack traces, raw prompts, or internal tool errors to the user.
- If context is missing, say what is missing and what to add.
- Prefer clear delivery language over abstract AI reasoning.

## Mattermost Task Thread Format

- Keep visible answers short.
- Mention the task key when useful.
- Use bullets for actions, risks, and acceptance criteria.
- If proposing a Plane update, create a draft and wait for approval.
- Approval commands: `y` or `да`.
- Rejection commands: `n` or `нет`.

## Plane Comment Format

Use short comments:

```text
Пользователь: Mattermost task thread
Запрос: ...
Изменение: ...
```

## AI Review Status Rules

- Green: task is clear enough for the agent role.
- Yellow: task can move forward but should be clarified.
- Red: task has blockers, contradictions, missing critical data, or cannot be implemented/tested safely.

## Safety Rules

- Never update Plane without explicit approval.
- Never delete user content.
- Do not overwrite the whole task description for AI suggestions.
- `!ac` may update only the Acceptance Criteria block and must mark new lines with `[AI]`.
"""
}


def find_plane_pages_project() -> Project:
    return Project.objects.select_related("workspace").get(
        workspace__slug=PLANE_PAGES_WORKSPACE_SLUG,
        identifier=PLANE_PAGES_PROJECT_IDENTIFIER,
        deleted_at__isnull=True,
        archived_at__isnull=True,
    )


def plane_page_source_path(project: Project, page: Page) -> str:
    return f"plane_pages/{project.identifier}/{page.id}.md"


def is_plane_page_public(page: Page) -> bool:
    return int(getattr(page, "access", 1) or 0) == 0


def is_plane_page_approved_for_rag(page: Page) -> bool:
    return (
        PLANE_PAGES_TITLE_MARKER.lower() in str(page.name or "").lower()
        and is_plane_page_public(page)
        and getattr(page, "deleted_at", None) is None
        and getattr(page, "archived_at", None) is None
    )


def page_body_text(page: Page) -> str:
    stripped = str(getattr(page, "description_stripped", "") or "").strip()
    if stripped:
        return stripped
    html = str(getattr(page, "description_html", "") or "").strip()
    return strip_tags(html).strip()


def plane_page_document_text(project: Project, page: Page) -> str:
    body = page_body_text(page)
    return f"""# {page.name}

Project: {project.name}
Project identifier: {project.identifier}
Plane page access: Public
Updated at: {page.updated_at.isoformat() if page.updated_at else ""}

{body}
""".strip()


def content_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def ensure_project_knowledge_page(project: Project, title: str, markdown: str) -> Page:
    existing_link = (
        ProjectPage.objects.select_related("page")
        .filter(
            workspace=project.workspace,
            project=project,
            page__name=title,
            page__deleted_at__isnull=True,
            page__archived_at__isnull=True,
            deleted_at__isnull=True,
        )
        .first()
    )
    owner = getattr(project, "project_lead", None) or getattr(project, "created_by", None) or getattr(project.workspace, "owner", None)
    if existing_link:
        page = existing_link.page
        changed_fields = []
        if not is_plane_page_public(page):
            page.access = 0
            changed_fields.append("access")
        if not page_body_text(page):
            page.description_html = markdown_to_basic_html(markdown)
            page.description_stripped = markdown
            page.description_json = text_to_description_json(markdown)
            page.updated_by = owner
            changed_fields.extend(["description_html", "description_stripped", "description_json", "updated_by"])
        if changed_fields:
            page.save(update_fields=sorted(set(changed_fields)))
        return page

    page = Page.objects.create(
        workspace=project.workspace,
        name=title,
        description_html=markdown_to_basic_html(markdown),
        description_stripped=markdown,
        description_json=text_to_description_json(markdown),
        owned_by=owner,
        access=0,
        created_by=owner,
        updated_by=owner,
    )
    ProjectPage.objects.create(
        workspace=project.workspace,
        project=project,
        page=page,
        created_by=owner,
        updated_by=owner,
    )
    return page


def ensure_plane_knowledge_templates(project: Project) -> list[Page]:
    return [
        ensure_project_knowledge_page(project, title, markdown)
        for title, markdown in PLANE_KNOWLEDGE_TEMPLATE_PAGES.items()
    ]


def plane_page_metadata(project: Project, page: Page, text: str) -> dict:
    digest = content_hash(text)
    author = getattr(getattr(page, "updated_by", None), "email", None) or getattr(getattr(page, "owned_by", None), "email", None) or "plane"
    return {
        "source_type": "plane_page",
        "collection": PLANE_PAGES_COLLECTION,
        "title": page.name,
        "author": author,
        "project": project.name,
        "tags": ["plane", "pages", "ai-approved", project.identifier],
        "created_at": page.created_at.isoformat() if page.created_at else utc_now_iso(),
        "updated_at": page.updated_at.isoformat() if page.updated_at else utc_now_iso(),
        "version": f"{page.updated_at.isoformat() if page.updated_at else utc_now_iso()}:{digest[:12]}",
        "language": "ru",
        "access_level": "public",
        "source_path": plane_page_source_path(project, page),
        "plane_page_id": str(page.id),
        "plane_project_id": str(project.id),
        "plane_project_identifier": project.identifier,
        "plane_page_access": "public",
        "content_sha256": digest,
        "dedupe": False,
    }


def sync_plane_pages_to_rag(payload: dict | None = None) -> dict:
    close_old_connections()
    started = time.time()
    project = find_plane_pages_project()
    if PLANE_PAGES_BOOTSTRAP_RULES:
        ensure_plane_knowledge_templates(project)

    links = (
        ProjectPage.objects.select_related("page")
        .filter(
            workspace=project.workspace,
            project=project,
            deleted_at__isnull=True,
            page__deleted_at__isnull=True,
            page__archived_at__isnull=True,
        )
        .order_by("page__updated_at")
    )
    seen_page_ids: set[str] = set()
    indexed = []
    skipped = []
    active_source_paths: list[str] = []

    for link in links:
        page = link.page
        page_id = str(page.id)
        if page_id in seen_page_ids:
            continue
        seen_page_ids.add(page_id)
        source_path = plane_page_source_path(project, page)
        if not is_plane_page_approved_for_rag(page):
            skipped.append({"page_id": page_id, "title": page.name, "reason": "not_public_or_missing_ai_marker"})
            continue
        text = plane_page_document_text(project, page)
        if not page_body_text(page):
            skipped.append({"page_id": page_id, "title": page.name, "reason": "empty_page"})
            continue
        metadata = plane_page_metadata(project, page, text)
        active_source_paths.append(source_path)
        rag_post("/rag/delete-source", {"collection": PLANE_PAGES_COLLECTION, "source_path": source_path}, timeout=60)
        result = rag_post(
            "/rag/ingest-text",
            {"collection": PLANE_PAGES_COLLECTION, "text": text, "metadata": metadata},
            timeout=120,
        )
        indexed.append({
            "page_id": page_id,
            "title": page.name,
            "source_path": source_path,
            "chunks": result.get("chunks", 0),
            "content_sha256": metadata["content_sha256"],
        })

    stale = rag_post(
        "/rag/delete-stale-sources",
        {
            "collection": PLANE_PAGES_COLLECTION,
            "source_type": "plane_page",
            "project": project.name,
            "active_source_paths": active_source_paths,
        },
        timeout=120,
    )
    event = {
        "event": "plane_pages_sync",
        "status": "success",
        "project": project.name,
        "collection": PLANE_PAGES_COLLECTION,
        "indexed": len(indexed),
        "skipped": len(skipped),
        "deleted": stale.get("deleted_count", 0),
        "duration_seconds": round(time.time() - started, 3),
    }
    append_execution_log(event)
    return {
        "ok": True,
        "project": project.name,
        "project_identifier": project.identifier,
        "collection": PLANE_PAGES_COLLECTION,
        "indexed": indexed,
        "skipped": skipped,
        "deleted": stale.get("deleted", []),
        "duration_seconds": event["duration_seconds"],
    }


def find_agent_review(review: dict, agent_name: str | None = None, agent_index: int | None = None) -> dict:
    agents = review.get("agents") if isinstance(review.get("agents"), list) else []
    if agent_name:
        for agent in agents:
            if agent.get("agent_name") == agent_name:
                return agent
    if agent_index is not None and 0 <= agent_index < len(agents):
        return agents[agent_index]
    raise ValueError("Agent review was not found")


def run_apply_review_suggestion(payload: dict) -> dict:
    if not REVIEW_GATE_ENABLED:
        return {"ok": False, "disabled": True, "error": "AI Review Gate is disabled"}

    review_id = str(payload.get("review_id") or "").strip()
    if not review_id:
        raise ValueError("Missing review_id")

    close_old_connections()
    issue = find_issue(payload)
    issue_payload = issue_to_payload(issue)
    issue_key = issue_payload["key"]
    review = find_review_history_item(issue_key, review_id)
    if not review:
        raise ValueError("Review history item was not found")

    agent_index = payload.get("agent_index")
    if agent_index is not None:
        agent_index = int(agent_index)
    agent = find_agent_review(review, payload.get("agent_name"), agent_index)

    finding = None
    finding_index = payload.get("finding_index")
    findings = agent.get("findings") if isinstance(agent.get("findings"), list) else []
    if finding_index is not None and findings:
        index = int(finding_index)
        if 0 <= index < len(findings):
            finding = findings[index]

    markdown_block = format_ai_comment_markdown(agent, finding, review.get("detected_type") or issue_payload.get("type") or "Task")
    before = {
        "description_html": issue.description_html or "",
        "description_stripped": issue.description_stripped or "",
        "labels": list(issue.labels.filter(deleted_at__isnull=True).values_list("name", flat=True)),
    }

    with transaction.atomic():
        comment = create_issue_comment(issue, markdown_block)
        label_name = mark_issue_with_ai_label(issue, "assisted")

    issue.refresh_from_db()
    after = {
        "description_html": issue.description_html or "",
        "description_stripped": issue.description_stripped or "",
        "labels": list(issue.labels.filter(deleted_at__isnull=True).values_list("name", flat=True)),
        "comment_id": str(comment.id),
    }
    apply_id = str(uuid.uuid4())
    event = {
        "ok": True,
        "apply_id": apply_id,
        "review_id": review_id,
        "issue_key": issue_key,
        "agent_name": agent.get("agent_name"),
        "finding": finding,
        "applied_at": utc_now_iso(),
        "applied_by": payload.get("applied_by") or "Plane UI",
        "comment_id": str(comment.id),
        "comment": markdown_block,
        "before": before,
        "after": after,
    }
    append_apply_history(event)
    append_execution_log({"event": "ai_review_apply", "status": "success", "issue_key": issue_key, "review_id": review_id, "apply_id": apply_id})
    return {
        "ok": True,
        "status": "applied",
        "apply_id": apply_id,
        "review_id": review_id,
        "issue_key": issue_key,
        "agent_name": agent.get("agent_name"),
        "label": label_name or "AI-A",
        "comment_id": str(comment.id),
        "message": "AI замечание добавлено в комментарии задачи.",
    }


def mattermost_api(path: str, method: str = "GET", payload=None) -> dict:
    if not MATTERMOST_BOT_TOKEN:
        raise RuntimeError("Mattermost bot token is not configured")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = Request(
        f"{MATTERMOST_INTERNAL_URL}{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {MATTERMOST_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urlopen(req, timeout=30) as response:
        body = response.read().decode("utf-8")
        if response.status >= 300:
            raise RuntimeError(f"Mattermost returned HTTP {response.status}")
        return json.loads(body or "{}")


def resolve_mattermost_user(username: str) -> dict:
    clean_username = (username or MATTERMOST_DEFAULT_USERNAME).strip().lstrip("@")
    if not clean_username:
        raise ValueError("Mattermost username is required")
    return mattermost_api(f"/api/v4/users/username/{quote(clean_username)}")


def create_direct_channel(target_user_id: str) -> dict:
    me = mattermost_api("/api/v4/users/me")
    return mattermost_api("/api/v4/channels/direct", "POST", [me["id"], target_user_id])


def post_mattermost_channel_message(channel_id: str, message: str, root_id: str | None = None) -> dict:
    payload = {"channel_id": channel_id, "message": message}
    if root_id:
        payload["root_id"] = root_id
    return mattermost_api("/api/v4/posts", "POST", payload)


def mattermost_current_user() -> dict:
    return mattermost_api("/api/v4/users/me")


def mattermost_thread_posts(root_id: str) -> list[dict]:
    data = mattermost_api(f"/api/v4/posts/{quote(root_id)}/thread")
    posts = data.get("posts") or {}
    order = data.get("order") or []
    if order:
        return [posts[post_id] for post_id in order if post_id in posts]
    return sorted(posts.values(), key=lambda item: item.get("create_at") or 0)


def latest_review_for_issue(issue_key: str) -> dict | None:
    reviews = read_review_history(issue_key, limit=1)
    return reviews[-1] if reviews else None


def summarize_review_agents(review: dict | None) -> str:
    if not review:
        return "- AI анализ ещё не найден. Запусти `AI анализ` в Plane, чтобы я получил свежие замечания."
    lines = []
    for agent in review.get("agents") or []:
        name = agent.get("agent_name") or "AI Agent"
        status = agent.get("status") or "yellow"
        summary = agent.get("summary") or "без краткого вывода"
        lines.append(f"- **{name}**: `{status}` — {summary}")
    return "\n".join(lines) if lines else "- В AI анализе нет агентских замечаний."


def summarize_context_graph(graph: dict) -> str:
    parts = []
    parents = graph.get("parents") or []
    if parents:
        parts.append("**Родительский контекст:**")
        for parent in parents:
            parts.append(f"- `{parent['key']}` {parent['title']} — {parent.get('state') or 'без статуса'}")
            if parent.get("description"):
                parts.append(f"  Требования/контекст: {parent['description'][:260]}")
    children = graph.get("children") or []
    if children:
        parts.append("**Дочерние задачи:**")
        for child in children[:8]:
            parts.append(f"- `{child['key']}` {child['title']} — {child.get('state') or 'без статуса'}")
    modules = graph.get("modules") or []
    if modules:
        parts.append("**Модули:**")
        for module in modules:
            detail = f" — {module['description'][:180]}" if module.get("description") else ""
            parts.append(f"- {module['name']} (`{module.get('status') or 'unknown'}`){detail}")
    cycles = graph.get("cycles") or []
    if cycles:
        parts.append("**Циклы:**")
        for cycle in cycles:
            parts.append(f"- {cycle['name']}")
    outgoing = (graph.get("relations") or {}).get("outgoing") or []
    incoming = (graph.get("relations") or {}).get("incoming") or []
    if outgoing or incoming:
        parts.append("**Связанные задачи:**")
        for relation in outgoing[:8]:
            related = relation.get("issue") or {}
            parts.append(f"- `{related.get('key')}` {related.get('title')} ({relation.get('relation_type')})")
        for relation in incoming[:8]:
            related = relation.get("issue") or {}
            parts.append(f"- `{related.get('key')}` {related.get('title')} (incoming: {relation.get('relation_type')})")
    return "\n".join(parts) if parts else "Связанный контекст пока не найден."


def format_task_chat_message(graph: dict, context_id: str) -> str:
    issue = graph["current"]
    review = graph.get("latest_review")
    issue_url = issue.get("url") or f"http://localhost:8080/{issue['workspace']['slug']}/browse/{issue['key']}"
    status = review.get("overall_status") if review else "not reviewed"
    detected_type = review.get("detected_type") if review else detect_issue_type(issue)
    agents = summarize_review_agents(review)
    graph_summary = summarize_context_graph(graph)
    return f"""**AIGILE Task Agent подключился к задаче** `{issue['key']}`

**Задача:** {issue['title']}
**Тип:** `{detected_type}`
**Статус AI review:** `{status}`
**Plane:** {issue_url}

Я буду держать контекст этой задачи в этом чате: описание, метки, статус, связи и последний AI review.

**Что уже вижу по агентам:**
{agents}

**Контекст вокруг задачи:**
{graph_summary}

Можешь отвечать сюда как в обычный чат по задаче. Если я подготовлю изменение для Plane, подтверди его коротко: `y` / `да`, или отклони: `n` / `нет`.

Контекст чата: `{context_id}`"""


def latest_contexts_by_root() -> dict[str, dict]:
    result = {}
    for context in read_task_chat_contexts():
        root_id = context.get("thread_root_id") or context.get("post_id")
        if root_id:
            result[root_id] = context
    return result


def task_chat_history(posts: list[dict], root_id: str, bot_user_id: str, limit: int | None = None) -> str:
    limit = limit or TASK_CHAT_HISTORY_LIMIT
    lines = []
    for post in posts:
        post_id = post.get("id")
        if not post_id or post_id == root_id:
            continue
        message = re.sub(r"\s+", " ", str(post.get("message") or "")).strip()
        if not message:
            continue
        speaker = "AI Agent" if post.get("user_id") == bot_user_id else "User"
        lines.append(f"{speaker}: {message[:900]}")
    return "\n".join(lines[-limit:])


def compact_graph_for_prompt(graph: dict) -> dict:
    current = graph.get("current") or {}
    review = graph.get("latest_review") or {}
    return {
        "current": current,
        "parents": graph.get("parents") or [],
        "children": graph.get("children") or [],
        "relations": graph.get("relations") or {},
        "cycles": graph.get("cycles") or [],
        "modules": graph.get("modules") or [],
        "latest_review": {
            "review_id": review.get("review_id"),
            "detected_type": review.get("detected_type"),
            "overall_status": review.get("overall_status"),
            "agents": [
                {
                    "agent_name": agent.get("agent_name"),
                    "status": agent.get("status"),
                    "summary": agent.get("summary"),
                    "findings": agent.get("findings") or [],
                }
                for agent in (review.get("agents") or [])
            ],
        }
        if review
        else None,
    }


def load_fresh_task_graph(context: dict) -> dict:
    issue_key = context.get("issue_key") or (context.get("issue") or {}).get("key")
    if issue_key:
        try:
            issue = find_issue({"workspace_slug": "aigile", "issue_key": issue_key})
            return build_issue_context_graph(issue)
        except Exception as exc:
            logger.warning("Failed to refresh task chat graph for %s: %s", issue_key, exc)
    return context.get("context_graph") or {"current": context.get("issue") or {}}


def generate_task_chat_reply(context: dict, user_message: str, thread_history: str) -> str:
    graph = load_fresh_task_graph(context)
    issue = dict(graph.get("current") or context.get("issue") or {})
    if graph.get("latest_review") and "latest_review" not in issue:
        issue["latest_review"] = graph.get("latest_review")
    project_pages_context = read_project_pages_context(
        build_task_chat_project_pages_query(issue, user_message, thread_history),
        limit=8,
    )
    prompt = f"""
You are the AIGILE Task Agent. The user is writing in a Mattermost thread dedicated to one Plane task.

Answer in English, concise and useful. Keep the context of the current task, parent epic, linked tasks,
modules, cycles, and latest AI Review. If the user asks to update Plane, do not apply the change directly:
prepare a draft and ask for confirmation with `y` / `yes`.
If Plane Pages project knowledge contains a template or response rule, use it strictly.

Task: {issue.get("key")} {issue.get("title")}

Task context:
{json.dumps(compact_graph_for_prompt(graph), ensure_ascii=False, indent=2)[:16000]}

Plane Pages project knowledge:
{project_pages_context or "No approved Plane Pages were found for this question."}

Thread history:
{thread_history or "No thread history yet."}

New user message:
{user_message}
"""
    try:
        raw = ollama_chat_completion(
            [
                {
                    "role": "system",
                    "content": "You are the local AIGILE AI agent for Plane task discussions in Mattermost. Answer in English. Do not use cloud APIs.",
                },
                {"role": "user", "content": prompt},
            ],
            {"temperature": 0.2},
        )
        answer = str(raw.get("message", {}).get("content") or "").strip()
    except Exception as exc:
        logger.exception("Task chat LLM reply failed")
        append_execution_log({
            "event": "task_chat_reply",
            "status": "failure",
            "issue_key": issue.get("key"),
            "error": str(exc),
        })
        answer = ""
    if not answer:
        answer = "I could not get a response from the local model. The task context is saved; please try again in a minute."
    return answer[:12000]


def normalize_chat_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def is_task_chat_approval(value: str) -> bool:
    text = normalize_chat_text(value).strip(" .!,;:")
    approval_terms = {"y", "yes", "да"}
    return text in approval_terms


def is_task_chat_cancel(value: str) -> bool:
    text = normalize_chat_text(value).strip(" .!,;:")
    cancel_terms = {"n", "no", "нет"}
    return text in cancel_terms


def is_task_chat_help(value: str) -> bool:
    text = normalize_chat_text(value).strip(" .!,;:")
    return text in {"!help", "/help", "help", "помощь", "что ты умеешь"}


def is_task_chat_status(value: str) -> bool:
    text = normalize_chat_text(value).strip(" .!,;:")
    return text in {"!status", "/status", "status", "статус", "контекст", "память"}


SIGNAL_COMMANDS = {
    "!risk": "risk",
    "!blocker": "blocker",
    "!dep": "dependency",
    "!dependency": "dependency",
    "!decision": "decision",
    "!question": "question",
    "!action": "action_item",
}


def parse_delivery_signal_command(value: str) -> dict | None:
    raw = str(value or "").strip()
    if not raw.startswith("!"):
        return None
    parts = raw.split(maxsplit=2)
    command = parts[0].lower()
    signal_type = SIGNAL_COMMANDS.get(command)
    if not signal_type:
        return None
    severity = "medium"
    text = ""
    if len(parts) >= 2:
        maybe_severity = parts[1].lower().strip(" .!,;:")
        if maybe_severity in {"low", "medium", "high", "critical"}:
            severity = maybe_severity
            text = parts[2].strip() if len(parts) >= 3 else ""
        else:
            text = raw[len(parts[0]):].strip()
    if not text:
        return {"ok": False, "command": command, "type": signal_type, "error": "Signal text is missing."}
    if signal_type == "blocker" and severity == "medium":
        severity = "critical"
    return {"ok": True, "command": command, "type": signal_type, "severity": severity, "text": text[:2000]}


def suggested_action_for_signal(signal_type: str, text: str) -> str:
    actions = {
        "risk": "Assign an owner and decide mitigation or acceptance.",
        "blocker": "Escalate today and define the next unblock action.",
        "dependency": "Confirm dependency owner, expected date, and fallback path.",
        "decision": "Record the decision in the task context and communicate impact.",
        "question": "Clarify with the right owner before moving the task forward.",
        "action_item": "Assign owner and due date.",
    }
    return actions.get(signal_type, "Review this signal with the task owner.")


def create_delivery_signal_from_context(context: dict, parsed: dict, post: dict | None = None) -> dict:
    graph = context.get("context_graph") if isinstance(context.get("context_graph"), dict) else {}
    issue = graph.get("current") or context.get("issue") or {}
    modules = graph.get("modules") or []
    module_names = [item.get("name") for item in modules if isinstance(item, dict) and item.get("name")]
    signal = {
        "id": str(uuid.uuid4()),
        "type": parsed["type"],
        "severity": parsed["severity"],
        "source": "mattermost_thread",
        "related_issue_key": issue.get("key") or context.get("issue_key"),
        "related_issue_id": issue.get("id") or (context.get("issue") or {}).get("id"),
        "team": "Not available",
        "module": ", ".join(module_names) if module_names else "Not available",
        "text": parsed["text"],
        "suggested_action": suggested_action_for_signal(parsed["type"], parsed["text"]),
        "created_at": utc_now_iso(),
        "status": "open",
        "mattermost": {
            "channel_id": context.get("channel_id"),
            "thread_root_id": context.get("thread_root_id"),
            "post_id": post.get("id") if isinstance(post, dict) else None,
            "user_id": post.get("user_id") if isinstance(post, dict) else None,
        },
    }
    append_delivery_signal(signal)
    append_execution_log({
        "event": "delivery_signal_created",
        "status": "success",
        "issue_key": signal.get("related_issue_key"),
        "signal_id": signal["id"],
        "signal_type": signal["type"],
        "source": signal["source"],
    })
    return signal


def format_delivery_signal_created(signal: dict) -> str:
    return f"""Delivery signal saved: `{signal.get("type")}` / `{signal.get("severity")}`.

Issue: `{signal.get("related_issue_key") or "unknown"}`
Status: `{signal.get("status") or "open"}`
Text: {signal.get("text") or ""}

It will appear on the Delivery Intelligence Dashboard."""


def looks_like_task_update_request(value: str) -> bool:
    text = normalize_chat_text(value)
    patterns = [
        "!ac",
        "!note",
        "!deadline",
        "добав",
        "обнов",
        "измени",
        "запиши",
        "зафиксируй",
        "поставь",
        "срок",
        "дедлайн",
        "acceptance",
        "criteria",
        "критер",
        "добавь это в задачу",
        "update task",
        "add to task",
    ]
    return any(pattern in text for pattern in patterns)


def format_task_chat_help() -> str:
    return """**AIGILE Task Agent**

I keep Plane task context in this thread: description, labels, status, parent Epic, links, module, cycle, and latest AI review.

Task update commands:

- `!ac text` - prepare Acceptance Criteria in the task description with `[AI]`;
- `!note text` - prepare a Plane comment;
- `!deadline text` - prepare a deadline note as a Plane comment.

Delivery signal commands for the dashboard:

- `!risk [low|medium|high|critical] text` - risk;
- `!blocker text` - blocker;
- `!dep text` - dependency;
- `!decision text` - decision;
- `!question text` - open question;
- `!action text` - action item.

Service commands:

- `!status` - show task memory;
- `!help` - show this guide.

I do not change Plane immediately. First I prepare a draft.
Apply: `y` or `да`.
Cancel: `n` or `нет`."""
    return """**AIGILE Task Agent**

Я держу контекст этой Plane-задачи в треде: описание, метки, статус, родительский эпик, связи, модуль, цикл и последний AI review.

Команды:

- `!ac` - acceptance criteria в описание задачи с пометкой `[AI]`;
- `!note` - заметка в комментарий;
- `!risk` - риск в комментарий;
- `!dep` - зависимость в комментарий;
- `!deadline` - заметка о сроке в комментарий;
- `!status` - что я помню по этой задаче;
- `!help` - показать эту памятку.

Я не меняю Plane сразу. Сначала готовлю черновик.
Применить: `y` или `да`.
Отменить: `n` или `нет`."""


def format_task_chat_status(context: dict, thread_state: dict) -> str:
    graph = context.get("context_graph") if isinstance(context.get("context_graph"), dict) else {}
    issue = graph.get("current") or context.get("issue") or {}
    parent_chain = graph.get("parent_chain") or []
    children = graph.get("children") or []
    relations = graph.get("relations") or {}
    outgoing = relations.get("outgoing") or []
    incoming = relations.get("incoming") or []
    modules = graph.get("modules") or []
    cycles = graph.get("cycles") or []
    latest_review = graph.get("latest_review") or {}
    pending = thread_state.get("pending_draft") if isinstance(thread_state.get("pending_draft"), dict) else None
    return f"""**Память по задаче**

Задача: `{issue.get("key") or context.get("issue_key")}` {issue.get("title") or ""}
Тип: `{issue.get("detected_type") or issue.get("type") or context.get("detected_type") or "unknown"}`
AI review: `{latest_review.get("overall_status") or context.get("overall_status") or "unknown"}`

Контекст:
- родительская цепочка: {len(parent_chain)}
- дочерние задачи: {len(children)}
- связи: {len(outgoing) + len(incoming)}
- модули: {len(modules)}
- циклы: {len(cycles)}
- сообщений в памяти треда: {len(thread_state.get("dialogue_history") or [])}

Черновик: {"есть, жду `y` / `да` или `n` / `нет`" if pending else "нет ожидающих изменений"}"""


def append_thread_dialogue(thread_state: dict, role: str, message: str, post_id: str | None = None) -> None:
    history = thread_state.setdefault("dialogue_history", [])
    history.append({
        "role": role,
        "message": str(message or "")[:4000],
        "post_id": post_id,
        "ts": utc_now_iso(),
    })
    del history[:-TASK_CHAT_HISTORY_LIMIT * 2]


def state_dialogue_history(thread_state: dict) -> str:
    lines = []
    for item in thread_state.get("dialogue_history") or []:
        role = item.get("role") or "message"
        message = re.sub(r"\s+", " ", str(item.get("message") or "")).strip()
        if message:
            lines.append(f"{role}: {message[:900]}")
    return "\n".join(lines[-TASK_CHAT_HISTORY_LIMIT:])


def detect_task_chat_command(value: str) -> dict:
    raw = str(value or "").strip()
    match = re.match(r"^!(ac|note|risk|dep|deadline)\b[:\s-]*(.*)$", raw, re.IGNORECASE | re.DOTALL)
    if match:
        command = match.group(1).lower()
    elif re.search(r"\baccept(?:a|e)nce\b|\bcriteria\b|критер", raw, re.IGNORECASE):
        command = "ac"
    else:
        command = "note"
    content = (match.group(2).strip() if match else raw)
    mapping = {
        "ac": {
            "command": "!ac",
            "section_title": "Acceptance criteria",
            "change_kind": "acceptance criteria",
        },
        "note": {
            "command": "!note",
            "section_title": "Task note",
            "change_kind": "task note",
        },
        "risk": {
            "command": "!risk",
            "section_title": "Risk",
            "change_kind": "risk note",
        },
        "dep": {
            "command": "!dep",
            "section_title": "Dependency",
            "change_kind": "dependency note",
        },
        "deadline": {
            "command": "!deadline",
            "section_title": "Deadline note",
            "change_kind": "deadline note",
        },
    }
    result = dict(mapping.get(command, mapping["note"]))
    result["content"] = content or raw
    return result


def is_referential_task_add_request(value: str) -> bool:
    text = normalize_chat_text(value)
    patterns = [
        "добавь их",
        "добавь это",
        "добавь в задачу",
        "добавь это в задачу",
        "запиши их",
        "зафиксируй их",
        "add them",
        "add this",
        "add it to task",
    ]
    return any(pattern in text for pattern in patterns)


def extract_recent_acceptance_criteria(thread_history: str) -> str:
    lines = [line.strip() for line in str(thread_history or "").splitlines() if line.strip()]
    for line in reversed(lines):
        lowered = line.lower()
        if not (line.startswith(("AI Agent:", "assistant:")) and ("[ai]" in lowered or "acceptance criteria" in lowered or "критер" in lowered)):
            continue
        text = re.sub(r"^(?:AI Agent|assistant):\s*", "", line, flags=re.IGNORECASE).strip()
        if re.search(r"\[AI\]", text, re.IGNORECASE):
            text = text[text.lower().find("[ai]") :]
            text = re.sub(r"\s*\[AI\]\s*", "\n", text, flags=re.IGNORECASE)
            items = [item.strip(" -:;") for item in text.splitlines() if item.strip(" -:;")]
            return "\n".join(items).strip()
        marker = re.search(r"accept(?:a|e)nce criteria|критерии приемки", text, re.IGNORECASE)
        if marker:
            return text[marker.end() :].strip(" :-")
    return ""


AC_HEADING_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s*)?(?:accept(?:a|e)nce criteria|критерии приемки)\s*:?\s*$", re.IGNORECASE)
MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S+")


def current_issue_description_text(issue: Issue) -> str:
    stripped = str(getattr(issue, "description_stripped", "") or "").strip()
    if stripped:
        return stripped
    html = str(getattr(issue, "description_html", "") or "")
    return strip_tags(html).strip()


def normalize_ac_requirement(value: str | None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^!ac\b[:\s-]*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def acceptance_criteria_items(value: str | None) -> list[str]:
    raw = str(value or "").strip()
    raw = re.sub(r"^!ac\b[:\s-]*", "", raw, flags=re.IGNORECASE).strip()
    if not raw:
        return []
    source_lines = raw.splitlines() if "\n" in raw else [raw]
    items = []
    for line in source_lines:
        text = re.sub(r"^\s*[-*]\s*", "", line).strip()
        text = re.sub(r"^\[AI\]\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            items.append(text)
    return items


def add_ai_acceptance_criteria(description: str, requirement: str) -> tuple[str, bool]:
    requirements = acceptance_criteria_items(requirement)
    if not requirements:
        return description, False
    existing = re.sub(r"\s+", " ", description or "").casefold()
    lines_to_add = []
    for item in requirements:
        comparable = re.sub(r"\s+", " ", item).casefold()
        if comparable and comparable not in existing:
            lines_to_add.append(f"- [AI] {item}")
            existing = f"{existing} {comparable}"
    if not lines_to_add:
        return description, False

    lines = (description or "").splitlines()
    heading_index = None
    for index, existing_line in enumerate(lines):
        if AC_HEADING_RE.match(existing_line):
            heading_index = index
            break

    if heading_index is None:
        next_lines = list(lines)
        if next_lines and next_lines[-1].strip():
            next_lines.append("")
        next_lines.extend(["## Acceptance Criteria", *lines_to_add])
        return "\n".join(next_lines).strip() + "\n", True

    insert_at = heading_index + 1
    while insert_at < len(lines):
        candidate = lines[insert_at]
        if insert_at > heading_index + 1 and MARKDOWN_HEADING_RE.match(candidate):
            break
        insert_at += 1
    next_lines = list(lines)
    for offset, line in enumerate(lines_to_add):
        next_lines.insert(insert_at + offset, line)
    return "\n".join(next_lines).strip() + "\n", True


def update_issue_description_text(issue: Issue, description: str) -> None:
    issue.description_stripped = description
    issue.description_html = markdown_to_basic_html(description)
    issue.description_json = text_to_description_json(description)
    issue.save(update_fields=["description_html", "description_stripped", "description_json"])


def format_task_chat_apply_summary(draft: dict, change_summary: str) -> str:
    requested = str(draft.get("requested_message") or "").strip()
    return f"""## AI summary

Пользователь: Mattermost task thread
Запрос: {requested}
Изменение: {change_summary}
"""


def build_task_update_draft(context: dict, user_message: str, thread_history: str) -> dict:
    graph = load_fresh_task_graph(context)
    issue = graph.get("current") or context.get("issue") or {}
    issue_key = issue.get("key") or context.get("issue_key")
    issue_title = issue.get("title") or "Plane task"
    draft_id = str(uuid.uuid4())
    command = detect_task_chat_command(user_message)
    resolved_from_thread = False
    if command.get("command") == "!note" and is_referential_task_add_request(user_message):
        resolved_acceptance_criteria = extract_recent_acceptance_criteria(thread_history)
        if resolved_acceptance_criteria:
            command = detect_task_chat_command(f"!ac {resolved_acceptance_criteria}")
            resolved_from_thread = True
    section_title = command.get("section_title")
    proposed_text = command.get("content") or user_message.strip()
    comment_markdown = f"""## AI draft from Mattermost

Task: `{issue_key}` {issue_title}

### {section_title}
{proposed_text}

### Proposed change
Add this {command.get("change_kind")} to the task history as an approved AI-assisted note. Do not overwrite the task description.

### Context used
- Parent items, linked tasks, modules, cycles, and latest AI review were available to the agent.
- Dialogue history in this Mattermost thread was considered.

### Safety
This draft will be added as a Plane comment only after explicit approval.
"""
    return {
        "draft_id": draft_id,
        "issue_key": issue_key,
        "issue_title": issue_title,
        "requested_message": user_message.strip(),
        "proposed_text": proposed_text,
        "command": command,
        "resolved_from_thread": resolved_from_thread,
        "thread_history": thread_history[-5000:],
        "comment_markdown": comment_markdown,
        "created_at": utc_now_iso(),
        "status": "pending_approval",
    }


def format_task_update_draft(draft: dict) -> str:
    command = (draft.get("command") or {}).get("command")
    if command == "!ac":
        apply_steps = """- обновлю блок `Acceptance Criteria` в описании задачи;
- добавлю новую строку с пометкой `[AI]`;
- поставлю метку `AIA`;
- оставлю короткое резюме в комментарии;
- сохраню событие в истории AI-действий."""
    else:
        apply_steps = """- добавлю это в задачу отдельным комментарием;
- не буду перетирать описание;
- поставлю метку `AI-A`;
- сохраню событие в истории AI-действий."""
    return f"""**Черновик изменения готов, но я его ещё не применил.**

Задача: `{draft.get("issue_key")}` {draft.get("issue_title")}

Что предлагаю добавить в Plane:

```text
{draft.get("proposed_text") or draft.get("requested_message") or ""}
```

Как применю после подтверждения:
{apply_steps}

Чтобы применить, ответь в этом треде: `y` или `да`.
Чтобы отменить: `n` или `нет`."""


def apply_task_update_draft(context: dict, draft: dict, approved_by: str) -> dict:
    issue_key = draft.get("issue_key") or context.get("issue_key")
    if not issue_key:
        raise ValueError("Draft has no issue key")
    issue = find_issue({"workspace_slug": "aigile", "issue_key": issue_key})
    command = draft.get("command") if isinstance(draft.get("command"), dict) else {}
    is_acceptance_criteria = command.get("command") == "!ac"
    description_updated = False
    duplicate = False
    if is_acceptance_criteria:
        before_description = current_issue_description_text(issue)
        updated_description, changed = add_ai_acceptance_criteria(before_description, command.get("content") or draft.get("proposed_text") or draft.get("requested_message") or "")
        if changed:
            update_issue_description_text(issue, updated_description)
            description_updated = True
            change_summary = "добавлен пункт Acceptance Criteria в описание задачи с пометкой [AI]."
        else:
            duplicate = True
            change_summary = "похожий пункт Acceptance Criteria уже был в описании, повторно не добавлял."
        comment_markdown = format_task_chat_apply_summary(draft, change_summary)
        label_name = mark_issue_with_ai_label(issue, "agent_assisted")
    else:
        comment_markdown = draft.get("comment_markdown") or draft.get("requested_message") or ""
        label_name = mark_issue_with_ai_label(issue, "assisted")
        change_summary = "добавлен approved AI-комментарий."
    comment = create_issue_comment(issue, comment_markdown)
    apply_id = str(uuid.uuid4())
    event = {
        "ok": True,
        "apply_id": apply_id,
        "draft_id": draft.get("draft_id"),
        "issue_key": issue_key,
        "approved_by": approved_by,
        "applied_at": utc_now_iso(),
        "comment_id": str(comment.id),
        "label": label_name or ("AIA" if is_acceptance_criteria else "AI-A"),
        "description_updated": description_updated,
        "duplicate": duplicate,
        "change_summary": change_summary,
        "source": "mattermost_task_thread",
        "draft": draft,
    }
    append_apply_history(event)
    append_execution_log({
        "event": "task_chat_apply_draft",
        "status": "success",
        "issue_key": issue_key,
        "apply_id": apply_id,
        "draft_id": draft.get("draft_id"),
    })
    return event


def poll_task_chat_threads(payload: dict | None = None) -> dict:
    close_old_connections()
    if not TASK_CHAT_THREAD_ENABLED and not (payload or {}).get("force"):
        return {"ok": True, "ignored": True, "message": "Task chat threads are disabled."}

    contexts = latest_contexts_by_root()
    if not contexts:
        return {"ok": True, "threads": 0, "replies": 0}

    bot_user = mattermost_current_user()
    bot_user_id = bot_user.get("id")
    state = read_task_chat_state()
    threads_state = state.setdefault("threads", {})
    replies = 0
    initialized = 0
    errors = []

    for root_id, context in contexts.items():
        channel_id = context.get("channel_id")
        if not channel_id:
            continue
        try:
            posts = mattermost_thread_posts(root_id)
        except Exception as exc:
            thread_state = threads_state.setdefault(root_id, {"context_id": context.get("context_id"), "processed_post_ids": [root_id]})
            thread_state["last_error"] = str(exc)
            thread_state["last_polled_at"] = utc_now_iso()
            errors.append({"root_id": root_id, "error": str(exc)})
            continue
        thread_state = threads_state.get(root_id)
        if not thread_state:
            threads_state[root_id] = {
                "context_id": context.get("context_id"),
                "processed_post_ids": sorted({post.get("id") for post in posts if post.get("id")}),
                "initialized_at": utc_now_iso(),
            }
            initialized += 1
            continue

        processed = set(thread_state.get("processed_post_ids") or [])
        processed.add(root_id)
        for post in sorted(posts, key=lambda item: item.get("create_at") or 0):
            post_id = post.get("id")
            if not post_id or post_id in processed:
                continue
            processed.add(post_id)
            message = str(post.get("message") or "").strip()
            if not message or post.get("user_id") == bot_user_id:
                continue
            append_thread_dialogue(thread_state, "user", message, post_id)
            live_history = task_chat_history(posts, root_id, bot_user_id)
            saved_history = state_dialogue_history(thread_state)
            history = "\n".join(item for item in [saved_history, live_history] if item).strip()
            pending_draft = thread_state.get("pending_draft") if isinstance(thread_state.get("pending_draft"), dict) else None
            if is_task_chat_help(message):
                answer = format_task_chat_help()
            elif is_task_chat_status(message):
                answer = format_task_chat_status(context, thread_state)
            elif (parsed_signal := parse_delivery_signal_command(message)):
                if not parsed_signal.get("ok"):
                    answer = (
                        "Не сохранил delivery signal: добавь текст после команды.\n\n"
                        "Пример: `!risk high есть риск не успеть к демо`."
                    )
                else:
                    signal = create_delivery_signal_from_context(context, parsed_signal, post)
                    answer = format_delivery_signal_created(signal)
            elif pending_draft and is_task_chat_cancel(message):
                thread_state.pop("pending_draft", None)
                answer = "Ок, черновик отменён. Plane не изменял."
            elif pending_draft and is_task_chat_approval(message):
                try:
                    applied = apply_task_update_draft(context, pending_draft, approved_by=post.get("user_id") or "mattermost")
                    thread_state.pop("pending_draft", None)
                    if applied.get("description_updated"):
                        answer = (
                            f"Готово. Обновил `Acceptance Criteria` в описании задачи `{applied['issue_key']}`, "
                            f"поставил метку `{applied['label']}` и оставил короткое резюме в комментарии."
                        )
                    elif applied.get("duplicate"):
                        answer = (
                            f"Готово. Похожий `Acceptance Criteria` уже был в `{applied['issue_key']}`, "
                            f"повторно не добавлял. Метка `{applied['label']}` поставлена, резюме оставил в комментарии."
                        )
                    else:
                        answer = (
                            f"Готово. Добавил approved AI-комментарий в Plane для `{applied['issue_key']}` "
                            f"и поставил метку `{applied['label']}`. Описание задачи не перетирал."
                        )
                except Exception as exc:
                    logger.exception("Task chat draft apply failed")
                    answer = f"Не смог применить черновик в Plane: {exc}. Черновик оставил в ожидании."
            elif looks_like_task_update_request(message):
                draft = build_task_update_draft(context, message, history)
                thread_state["pending_draft"] = draft
                answer = format_task_update_draft(draft)
            elif pending_draft and not is_task_chat_approval(message):
                answer = "У меня уже есть черновик изменения по этой задаче. Ответь `y` / `да`, чтобы применить, или `n` / `нет`, чтобы отменить."
            else:
                answer = generate_task_chat_reply(context, message, history)
            reply = post_mattermost_channel_message(channel_id, answer, root_id=root_id)
            if reply.get("id"):
                processed.add(reply["id"])
                append_thread_dialogue(thread_state, "assistant", answer, reply["id"])
            replies += 1
            append_execution_log({
                "event": "task_chat_reply",
                "status": "success",
                "issue_key": context.get("issue_key"),
                "context_id": context.get("context_id"),
                "root_id": root_id,
                "post_id": post_id,
                "reply_id": reply.get("id"),
            })

        thread_state["processed_post_ids"] = sorted(processed)
        thread_state["last_polled_at"] = utc_now_iso()

    write_task_chat_state(state)
    return {"ok": True, "threads": len(contexts), "initialized": initialized, "replies": replies, "errors": len(errors)}


def task_chat_poll_loop() -> None:
    while True:
        try:
            poll_task_chat_threads({})
        except Exception as exc:
            logger.exception("Task chat thread poll failed")
            append_execution_log({"event": "task_chat_poll", "status": "failure", "error": str(exc)})
        time.sleep(max(3, TASK_CHAT_POLL_SECONDS))


def run_start_task_chat(payload: dict) -> dict:
    close_old_connections()
    issue = find_issue(payload)
    issue_payload = issue_to_payload(issue)
    issue_key = issue_payload["key"]
    review_id = str(payload.get("review_id") or "").strip()
    graph = build_issue_context_graph(issue)
    if review_id:
        graph["latest_review"] = find_review_history_item(issue_key, review_id)
    username = str(payload.get("mattermost_username") or MATTERMOST_DEFAULT_USERNAME).strip().lstrip("@")
    target_user = resolve_mattermost_user(username)
    channel = create_direct_channel(target_user["id"])
    context_id = str(uuid.uuid4())
    message = format_task_chat_message(graph, context_id)
    post = post_mattermost_channel_message(channel["id"], message)
    thread_root_id = post.get("id")
    event = {
        "ok": True,
        "context_id": context_id,
        "issue_key": issue_key,
        "issue": issue_payload,
        "context_graph": graph,
        "review_id": graph["latest_review"].get("review_id") if graph.get("latest_review") else None,
        "mattermost_username": username,
        "mattermost_user_id": target_user["id"],
        "channel_id": channel["id"],
        "post_id": post.get("id"),
        "thread_root_id": thread_root_id,
        "started_at": utc_now_iso(),
        "mode": "task_chat_thread_mvp_1",
    }
    append_task_chat_context(event)
    mark_task_chat_thread_started(thread_root_id, context_id)
    append_execution_log({"event": "task_chat_start", "status": "success", "issue_key": issue_key, "context_id": context_id})
    return {
        "ok": True,
        "status": "sent",
        "context_id": context_id,
        "issue_key": issue_key,
        "mattermost_username": username,
        "channel_id": channel["id"],
        "post_id": post.get("id"),
        "thread_root_id": thread_root_id,
        "message": "Task chat started in Mattermost.",
    }


def normalize_finding(value: dict | None) -> dict:
    value = value if isinstance(value, dict) else {}
    severity = str(value.get("severity") or "medium").lower()
    if severity not in {"low", "medium", "high"}:
        severity = "medium"
    return {
        "severity": severity,
        "title": str(value.get("title") or "AI review finding"),
        "description": str(value.get("description") or ""),
        "recommendation": str(value.get("recommendation") or ""),
        "can_be_applied": bool(value.get("can_be_applied", False)),
    }


def normalize_agent_review(agent_name: str, raw: dict | str) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return safe_agent_fallback(agent_name, f"Agent returned invalid JSON: {raw[:500]}")
    if not isinstance(raw, dict):
        return safe_agent_fallback(agent_name, "Agent returned a non-object response.")

    status = str(raw.get("status") or "yellow").lower()
    if status not in {"green", "yellow", "red"}:
        status = "yellow"
    findings = [normalize_finding(item) for item in raw.get("findings") or [] if isinstance(item, dict)]
    patch = raw.get("proposed_task_patch") if isinstance(raw.get("proposed_task_patch"), dict) else {}
    normalized_patch = default_patch()
    normalized_patch.update({key: patch.get(key, normalized_patch[key]) for key in normalized_patch})
    return {
        "agent_name": str(raw.get("agent_name") or agent_name),
        "status": status,
        "summary": str(raw.get("summary") or "AI review completed."),
        "findings": findings,
        "proposed_task_patch": normalized_patch,
    }


def safe_agent_fallback(agent_name: str, reason: str) -> dict:
    return {
        "agent_name": agent_name,
        "status": "yellow",
        "summary": "Agent review could not be parsed safely.",
        "findings": [
            {
                "severity": "medium",
                "title": "Invalid agent response",
                "description": reason,
                "recommendation": "Review the task manually or rerun AI analysis.",
                "can_be_applied": False,
            }
        ],
        "proposed_task_patch": default_patch(),
    }


def strip_code_fence(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()
    return content


def review_agent(agent_name: str, issue_type: str, issue: dict, context: str) -> dict:
    prompt = f"""
You are {agent_name} reviewing a Plane task for AIGILE.
Task type: {issue_type}

Plane Pages Project Knowledge is strict. If it contains agent rules, templates, or review criteria,
you must follow them over generic assumptions.

Return STRICT JSON only with this shape:
{{
  "agent_name": "{agent_name}",
  "status": "green | yellow | red",
  "summary": "short English summary",
  "findings": [
    {{
      "severity": "low | medium | high",
      "title": "finding title",
      "description": "problem description",
      "recommendation": "what should be changed",
      "can_be_applied": true
    }}
  ],
  "proposed_task_patch": {{
    "title": "",
    "description": "",
    "acceptance_criteria": [],
    "test_cases": [],
    "risks": [],
    "dependencies": []
  }}
}}

Status rules:
- green: no required changes for your role.
- yellow: useful improvements, but task can proceed.
- red: blocker, contradiction, missing critical info, or serious risk.

Local knowledge context:
{context[:8000]}

Issue:
{json.dumps(issue, ensure_ascii=False, indent=2)}
""".strip()
    raw = ollama_chat_completion(
        [
            {"role": "system", "content": "Return strict JSON only in English. No markdown. No cloud APIs."},
            {"role": "user", "content": prompt},
        ],
        {"temperature": 0.1},
    )
    content = strip_code_fence(raw.get("message", {}).get("content", "{}"))
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return safe_agent_fallback(agent_name, content[:1000])
    return normalize_agent_review(agent_name, parsed)


def review_agents_batch(agent_names: list[str], issue_type: str, issue: dict, context: str) -> list[dict]:
    prompt = f"""
You are the AIGILE AI Review Gate.
Review one Plane task from the perspective of every listed agent.

Task type: {issue_type}
Agents: {", ".join(agent_names)}

Plane Pages Project Knowledge is strict. If it contains agent rules, templates, or review criteria,
follow them over generic assumptions.

Return STRICT JSON only with this shape:
{{
  "agents": [
    {{
      "agent_name": "one of the requested agent names",
      "status": "green | yellow | red",
      "summary": "short English summary",
      "findings": [
        {{
          "severity": "low | medium | high",
          "title": "finding title",
          "description": "problem description",
          "recommendation": "what should be changed",
          "can_be_applied": true
        }}
      ],
      "proposed_task_patch": {{
        "title": "",
        "description": "",
        "acceptance_criteria": [],
        "test_cases": [],
        "risks": [],
        "dependencies": []
      }}
    }}
  ]
}}

Rules:
- Return exactly one review object for each requested agent.
- Use green only when no required changes exist for that role.
- Use yellow for useful improvements when the task can proceed.
- Use red for blockers, contradictions, missing critical info, or serious risk.
- Do not add markdown or commentary outside JSON.

Local knowledge context:
{context[:8000]}

Issue:
{json.dumps(issue, ensure_ascii=False, indent=2)}
""".strip()
    raw = ollama_chat_completion(
        [
            {"role": "system", "content": "Return strict JSON only in English. No markdown. No cloud APIs."},
            {"role": "user", "content": prompt},
        ],
        {"temperature": 0.1},
    )
    content = strip_code_fence(raw.get("message", {}).get("content", "{}"))
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return [safe_agent_fallback(agent_name, content[:1000]) for agent_name in agent_names]

    raw_agents = parsed.get("agents") if isinstance(parsed, dict) else parsed
    if not isinstance(raw_agents, list):
        return [safe_agent_fallback(agent_name, "Batch review did not return an agents array.") for agent_name in agent_names]

    by_name = {
        str(item.get("agent_name") or "").strip().lower(): item
        for item in raw_agents
        if isinstance(item, dict)
    }
    normalized = []
    for agent_name in agent_names:
        item = by_name.get(agent_name.lower())
        if not item:
            normalized.append(safe_agent_fallback(agent_name, "Batch review did not include this agent."))
        else:
            normalized.append(normalize_agent_review(agent_name, item))
    return normalized


def review_agents(agent_names: list[str], issue_type: str, issue: dict, context: str) -> list[dict]:
    if not agent_names:
        return []

    if os.environ.get("AIGILE_REVIEW_BATCH_ENABLED", "true").lower() == "true":
        return review_agents_batch(agent_names, issue_type, issue, context)

    max_workers = max(1, min(len(agent_names), int(os.environ.get("AIGILE_REVIEW_MAX_WORKERS", "3"))))
    results: list[dict | None] = [None] * len(agent_names)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(review_agent, agent_name, issue_type, issue, context): index
            for index, agent_name in enumerate(agent_names)
        }
        for future in as_completed(futures):
            index = futures[future]
            agent_name = agent_names[index]
            try:
                results[index] = future.result()
            except Exception as exc:
                logger.warning("Agent review failed for %s: %s", agent_name, exc)
                results[index] = safe_agent_fallback(agent_name, str(exc))

    return [result for result in results if result is not None]


def run_review_gate(payload: dict) -> dict:
    if not REVIEW_GATE_ENABLED:
        return {"ok": False, "disabled": True, "error": "AI Review Gate is disabled"}

    close_old_connections()
    issue = find_issue(payload)
    issue_payload = issue_to_payload(issue)
    issue_key = issue_payload["key"]
    if not has_type_label(issue_payload):
        append_execution_log({"event": "ai_review_gate", "status": "blocked", "issue_key": issue_key, "reason": "missing_type_label"})
        return {
            "ok": False,
            "blocked": True,
            "code": "missing_type_label",
            "issue_key": issue_key,
            "title": "Анализ заблокирован",
            "message": TYPE_LABEL_ERROR,
            "error": TYPE_LABEL_ERROR,
            "required_labels": TYPE_LABEL_NAMES,
        }
    detected_type = detect_issue_type(issue_payload)
    agent_names = agents_for_issue_type(detected_type)
    context = build_review_context(issue_payload, detected_type, agent_names)
    agents = review_agents(agent_names, detected_type, issue_payload, context)
    gate_review = deterministic_gate_review(detected_type, issue_payload)
    if gate_review:
        agents.insert(0, gate_review)
    review = {
        "ok": True,
        "review_id": str(uuid.uuid4()),
        "issue_id": issue_payload["id"],
        "issue_key": issue_key,
        "detected_type": detected_type,
        "overall_status": overall_review_status(agents),
        "created_at": utc_now_iso(),
        "model": OLLAMA_MODEL,
        "agents": agents,
    }
    append_review_history(review)
    mark_issue_with_ai_label(issue, "reviewed")
    append_execution_log({"event": "ai_review_gate", "status": "success", "issue_key": issue_key, "review_id": review["review_id"]})
    return review


def format_mattermost_message(issue: dict, analysis: dict) -> str:
    title = issue["title"]
    key = issue["key"]
    preview = analysis.get("preview_summary") or "AI-анализ готов."
    status = analysis.get("status") or "ready"
    risks = "\n".join(f"- {item}" for item in as_list(analysis.get("risks"))) or "- No major risks identified"
    deps = "\n".join(f"- {item}" for item in as_list(analysis.get("dependencies"))) or "- No explicit dependencies"
    ac = "\n".join(f"- {item}" for item in as_list(analysis.get("acceptance_criteria"))) or "- Acceptance criteria need clarification"
    plan = "\n".join(f"- {item}" for item in as_list(analysis.get("implementation_plan"))) or "- Implementation plan needs clarification"
    full = analysis.get("full_analysis") or preview
    codex_prompt = analysis.get("codex_prompt") or f"Implement Plane issue {key}: {title}"
    return f"""**AI анализ задачи готов:** `{key}` {title}

Кратко: {preview}

Status: `{status}`

<details>
<summary>Показать полный AI-анализ</summary>

## Полный анализ

{full}

## Risks
{risks}

## Dependencies
{deps}

## Acceptance criteria
{ac}

## Suggested implementation plan
{plan}

## Codex-ready prompt

```text
{codex_prompt}
```

</details>"""


def post_to_mattermost(text: str) -> None:
    body = {"username": "AI Delivery Assistant", "text": text}
    req = Request(
        MATTERMOST_WEBHOOK_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=30) as response:
        if response.status >= 300:
            raise RuntimeError(f"Mattermost returned HTTP {response.status}")


def run_manual_trigger(payload: dict) -> dict:
    close_old_connections()
    issue = find_issue(payload)
    issue_key = f"{issue.project.identifier}-{issue.sequence_id}"

    with IN_FLIGHT_LOCK:
        if issue_key in IN_FLIGHT:
            return {"ok": True, "status": "already_running", "issue_key": issue_key}
        IN_FLIGHT.add(issue_key)

    try:
        issue_payload = issue_to_payload(issue)
        context = read_knowledge_base()
        analysis = ollama_chat(issue_payload, context)
        message = format_mattermost_message(issue_payload, analysis)
        post_to_mattermost(message)
        append_execution_log({"event": "manual_trigger", "status": "success", "issue_key": issue_key})
        return {
            "ok": True,
            "status": "sent",
            "issue_key": issue_key,
            "preview_summary": analysis.get("preview_summary"),
        }
    except Exception as exc:
        append_execution_log({"event": "manual_trigger", "status": "failure", "issue_key": issue_key, "error": str(exc)})
        raise
    finally:
        with IN_FLIGHT_LOCK:
            IN_FLIGHT.discard(issue_key)


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        json_response(self, 204, {})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            json_response(self, 200, {"ok": True})
            return
        if parsed.path == "/healthz":
            report = build_health_report()
            json_response(self, 200 if report.get("ok") else 503, report)
            return
        if parsed.path in {"/dashboard", "/health-dashboard"}:
            report = build_health_report()
            html_response(self, 200, render_health_dashboard(report))
            return
        if parsed.path == "/delivery-dashboard":
            report = build_delivery_intelligence_report()
            html_response(self, 200, render_delivery_intelligence_dashboard(report))
            return
        if parsed.path in {"/daily-delivery-brief", "/morning-brief"}:
            report = build_delivery_intelligence_report()
            brief = build_daily_delivery_brief(report)
            html_response(self, 200, render_daily_delivery_brief(brief))
            return
        if parsed.path == "/api/delivery-intelligence":
            report = build_delivery_intelligence_report()
            json_response(self, 200, report)
            return
        if parsed.path == "/api/daily-delivery-brief":
            report = build_delivery_intelligence_report()
            brief = build_daily_delivery_brief(report)
            json_response(self, 200, brief)
            return
        if parsed.path == "/api/delivery-signals":
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            status = params.get("status")
            limit = int(params.get("limit") or 500)
            json_response(self, 200, {"ok": True, "signals": read_delivery_signals(status=status, limit=limit)})
            return
        if parsed.path == "/api/resolve-issue":
            try:
                payload = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                issue = find_issue(payload)
                json_response(self, 200, {"ok": True, "issue": issue_to_payload(issue)})
            except Exception as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/review-history":
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            issue_key = params.get("issue_key")
            limit = int(params.get("limit") or 20)
            json_response(self, 200, {"ok": True, "reviews": read_review_history(issue_key, limit)})
            return
        json_response(self, 404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/manual-trigger":
            try:
                payload = read_body(self)
                result = run_manual_trigger(payload)
                json_response(self, 200, result)
            except ValueError as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                logger.exception("Manual trigger failed")
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/review-task":
            try:
                payload = read_body(self)
                result = run_review_gate(payload)
                json_response(self, 200 if result.get("ok") else 400, result)
            except ValueError as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                logger.exception("AI review gate failed")
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/apply-review-suggestion":
            try:
                payload = read_body(self)
                result = run_apply_review_suggestion(payload)
                json_response(self, 200 if result.get("ok") else 400, result)
            except ValueError as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                logger.exception("AI review suggestion apply failed")
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/start-task-chat":
            try:
                payload = read_body(self)
                result = run_start_task_chat(payload)
                json_response(self, 200 if result.get("ok") else 400, result)
            except ValueError as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                logger.exception("Task chat start failed")
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/task-chat/poll":
            try:
                payload = read_body(self)
                result = poll_task_chat_threads(payload)
                json_response(self, 200 if result.get("ok") else 400, result)
            except Exception as exc:
                logger.exception("Task chat poll failed")
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/delivery-signals/status":
            try:
                payload = read_body(self)
                result = update_delivery_signal_status(payload.get("id"), payload.get("status"), payload.get("updated_by") or "api")
                json_response(self, 200, result)
            except ValueError as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                logger.exception("Delivery signal status update failed")
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/daily-delivery-brief/send":
            try:
                payload = read_body(self)
                channel_id = payload.get("channel_id")
                if not channel_id:
                    raise ValueError("channel_id is required")
                report = build_delivery_intelligence_report()
                brief = build_daily_delivery_brief(report)
                message = format_daily_delivery_brief_mattermost(brief)
                post = post_mattermost_channel_message(channel_id, message)
                json_response(self, 200, {"ok": True, "status": "sent", "post_id": post.get("id"), "brief": brief})
            except ValueError as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                logger.exception("Daily delivery brief send failed")
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/refresh-knowledge-base":
            try:
                path = refresh_knowledge_base()
                json_response(self, 200, {"ok": True, "path": path})
            except Exception as exc:
                logger.exception("Manual KB refresh failed")
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/sync-plane-pages":
            try:
                payload = read_body(self)
                result = sync_plane_pages_to_rag(payload)
                json_response(self, 200 if result.get("ok") else 400, result)
            except Exception as exc:
                logger.exception("Plane Pages sync failed")
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/demo/seed":
            try:
                result = seed_demo_data(reset=False)
                json_response(self, 200, result)
            except Exception as exc:
                logger.exception("Demo seed failed")
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/demo/reset":
            try:
                result = seed_demo_data(reset=True)
                json_response(self, 200, result)
            except Exception as exc:
                logger.exception("Demo reset failed")
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        json_response(self, 404, {"ok": False, "error": "Not found"})

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in {"seed-demo", "reset-demo"}:
        ensure_dirs()
        result = seed_demo_data(reset=sys.argv[1] == "reset-demo")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    ensure_dirs()
    threading.Thread(target=initial_refresh_loop, daemon=True).start()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    threading.Thread(target=task_chat_poll_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    logger.info("AIGILE backend listening on 0.0.0.0:%s", PORT)
    server.serve_forever()
