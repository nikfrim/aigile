import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import aigile_backend


class FakeIssue:
    def __init__(self, key="AIGILE", sequence_id=1):
        self.project = mock.Mock(identifier=key)
        self.sequence_id = sequence_id


class FakeLabelQuery:
    def __init__(self, names):
        self.names = names

    def filter(self, **kwargs):
        return self

    def values_list(self, *args, **kwargs):
        return self.names


class FakeIssueForApply(FakeIssue):
    def __init__(self):
        super().__init__()
        self.id = "issue-1"
        self.workspace = mock.Mock(slug="aigile")
        self.project = mock.Mock(identifier="AIGILE")
        self.description_html = "<p>Original</p>"
        self.description_stripped = "Original"
        self.description_json = {}
        self.labels = FakeLabelQuery(["type: Story"])
        self.saved = False

    def save(self, update_fields=None):
        self.saved = True

    def refresh_from_db(self):
        return None


class MattermostFormattingTests(unittest.TestCase):
    def test_payload_has_visible_preview_and_collapsible_full_analysis(self):
        issue = {"key": "AIGILE-1", "title": "Manual trigger"}
        analysis = {
            "preview_summary": "Needs clearer acceptance criteria.",
            "status": "ready",
            "full_analysis": "Full analysis text",
            "risks": ["Risk one"],
            "dependencies": ["Dependency one"],
            "acceptance_criteria": ["AC one"],
            "implementation_plan": ["Step one"],
            "codex_prompt": "Implement this task",
        }

        message = aigile_backend.format_mattermost_message(issue, analysis)

        self.assertIn("**AI анализ задачи готов:**", message)
        self.assertIn("Needs clearer acceptance criteria.", message)
        self.assertIn("<details>", message)
        self.assertIn("<summary>Показать полный AI-анализ</summary>", message)
        self.assertIn("Implement this task", message)


class ManualTriggerTests(unittest.TestCase):
    def test_missing_issue_id_raises_clear_error(self):
        with self.assertRaisesRegex(ValueError, "Missing issue_id or issue_key"):
            aigile_backend.find_issue({})

    def test_successful_manual_trigger(self):
        with (
            mock.patch.object(aigile_backend, "find_issue", return_value=FakeIssue()),
            mock.patch.object(aigile_backend, "issue_to_payload", return_value={"key": "AIGILE-1", "title": "Task"}),
            mock.patch.object(aigile_backend, "read_knowledge_base", return_value="kb"),
            mock.patch.object(aigile_backend, "ollama_chat", return_value={"preview_summary": "ok", "status": "ready"}),
            mock.patch.object(aigile_backend, "post_to_mattermost") as post,
            mock.patch.object(aigile_backend, "append_execution_log"),
        ):
            result = aigile_backend.run_manual_trigger({"issue_key": "AIGILE-1"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "sent")
        post.assert_called_once()

    def test_repeated_click_returns_already_running(self):
        with (
            mock.patch.object(aigile_backend, "find_issue", return_value=FakeIssue()),
            mock.patch.object(aigile_backend, "IN_FLIGHT", {"AIGILE-1"}),
        ):
            result = aigile_backend.run_manual_trigger({"issue_key": "AIGILE-1"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "already_running")

    def test_failed_mattermost_send_bubbles_up(self):
        with (
            mock.patch.object(aigile_backend, "find_issue", return_value=FakeIssue()),
            mock.patch.object(aigile_backend, "issue_to_payload", return_value={"key": "AIGILE-1", "title": "Task"}),
            mock.patch.object(aigile_backend, "read_knowledge_base", return_value="kb"),
            mock.patch.object(aigile_backend, "ollama_chat", return_value={"preview_summary": "ok", "status": "ready"}),
            mock.patch.object(aigile_backend, "post_to_mattermost", side_effect=RuntimeError("Mattermost down")),
            mock.patch.object(aigile_backend, "append_execution_log"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Mattermost down"):
                aigile_backend.run_manual_trigger({"issue_key": "AIGILE-1"})


class HealthDashboardTests(unittest.TestCase):
    def test_http_probe_marks_2xx_service_ok(self):
        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, *args):
                return b"ok"

            def getcode(self):
                return 200

        with mock.patch.object(aigile_backend, "urlopen", return_value=FakeResponse()):
            result = aigile_backend.probe_http_service("svc", "Service", "http://service/health")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["status_code"], 200)

    def test_health_report_returns_degraded_when_any_service_is_down(self):
        ok_service = {"id": "svc", "name": "Service", "ok": True, "status": "ok"}
        down_service = {"id": "svc-down", "name": "Down", "ok": False, "status": "down"}

        with (
            mock.patch.object(aigile_backend, "probe_plane_database", return_value=ok_service),
            mock.patch.object(
                aigile_backend,
                "probe_http_service",
                side_effect=[ok_service, ok_service, ok_service, ok_service, down_service, ok_service, ok_service],
            ),
        ):
            report = aigile_backend.build_health_report()

        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["services_down"], 1)


class DemoSeedTests(unittest.TestCase):
    def test_demo_issue_specs_are_predictable_and_typed(self):
        self.assertEqual(len(aigile_backend.DEMO_ISSUES), 4)
        type_labels = {spec["type_label"] for spec in aigile_backend.DEMO_ISSUES}
        self.assertEqual(type_labels, {"Story", "Bug", "Epic", "Tech Debt"})
        for spec in aigile_backend.DEMO_ISSUES:
            self.assertTrue(spec["title"].startswith("[DEMO] "))
            self.assertIn("Known Gaps For Demo", spec["description"])
            self.assertIn(spec["type_label"], aigile_backend.KNOWN_ISSUE_TYPES.values())

    def test_demo_label_and_prefix_are_stable(self):
        self.assertEqual(aigile_backend.DEMO_LABEL_NAME, "AIGILE-DEMO")
        self.assertEqual(aigile_backend.DEMO_TITLE_PREFIX, "[DEMO]")


class DeliveryIntelligenceTests(unittest.TestCase):
    def test_delivery_dashboard_render_contains_management_sections(self):
        report = {
            "created_at": "2026-06-02T08:00:00Z",
            "project": "AIGILE Platform",
            "overall_status": "yellow",
            "morning_brief": {
                "mode": "rule_based",
                "findings": ["1 reviewed task has yellow AI review."],
            },
            "delivery_health": {
                "reviewed_total": 1,
                "unreviewed_total": 2,
                "waiting_human_approval": 0,
                "status_counts": {"green": 0, "yellow": 1, "red": 0},
            },
            "top_risks": [],
            "blockers": [],
            "requirement_quality": {
                "without_acceptance_criteria": {"count": 2, "items": []},
                "without_type_label": {"count": 1, "items": []},
                "missing_info": {"count": 1, "items": []},
                "yellow_red_qa_review": {"count": 1, "items": []},
                "risks_or_dependencies_detected": {"count": 0, "items": []},
            },
            "module_signals": [],
            "decisions_needed": [],
            "changes_since_yesterday": {"message": "Historical comparison is not available yet."},
            "suggested_actions": ["Run AI analysis for high-priority tasks."],
            "data_sources": {"plane_issues": True},
        }

        html = aigile_backend.render_delivery_intelligence_dashboard(report)

        self.assertIn("AIGILE Delivery Intelligence", html)
        self.assertIn("Morning Brief Summary", html)
        self.assertIn("Top Risks", html)
        self.assertIn("Requirement Quality", html)
        self.assertIn("Decisions Needed", html)
        self.assertIn("/api/delivery-intelligence", html)


class ReviewGateTests(unittest.TestCase):
    def test_detect_issue_type_from_type_label_title_and_fallback(self):
        self.assertEqual(aigile_backend.detect_issue_type({"type": "Story"}), "Story")
        self.assertEqual(aigile_backend.detect_issue_type({"labels": ["backend", "bug"]}), "Bug")
        self.assertEqual(aigile_backend.detect_issue_type({"labels": ["type: Bug"]}), "Bug")
        self.assertEqual(aigile_backend.detect_issue_type({"labels": ["type: Story"]}), "Story")
        self.assertEqual(aigile_backend.detect_issue_type({"title": "[Research] RAG experiment"}), "Research")
        self.assertEqual(aigile_backend.detect_issue_type({"type": "Task", "labels": ["баг"], "title": "Баг в проекте не открывается авторизация"}), "Bug")
        self.assertEqual(aigile_backend.detect_issue_type({"type": "Task", "title": "Баг в проекте не открывается авторизация"}), "Task")
        self.assertEqual(aigile_backend.detect_issue_type({"labels": ["ошибка"]}), "Bug")
        self.assertEqual(aigile_backend.detect_issue_type({"title": "Техдолг: обновить docker compose"}), "Tech Debt")
        self.assertEqual(aigile_backend.detect_issue_type({"title": "Plain task"}), "Task")

    def test_agents_for_story_bug_and_task(self):
        self.assertIn("Product Owner Agent", aigile_backend.agents_for_issue_type("Story"))
        self.assertIn("Backend Developer Agent", aigile_backend.agents_for_issue_type("Bug"))
        self.assertEqual(aigile_backend.agents_for_issue_type("Task"), ["Delivery Manager Agent", "Tech Lead Agent", "QA Engineer Agent"])

    def test_overall_review_status(self):
        self.assertEqual(aigile_backend.overall_review_status([{"status": "green"}]), "green")
        self.assertEqual(aigile_backend.overall_review_status([{"status": "green"}, {"status": "yellow"}]), "yellow")
        self.assertEqual(aigile_backend.overall_review_status([{"status": "yellow"}, {"status": "red"}]), "red")

    def test_deterministic_gate_marks_title_only_task_red(self):
        review = aigile_backend.deterministic_gate_review(
            "Task",
            {"title": "Login fails", "description": ""},
        )

        self.assertIsNotNone(review)
        self.assertEqual(review["agent_name"], "AIGILE Review Gate")
        self.assertEqual(review["status"], "red")
        self.assertTrue(any(item["severity"] == "high" for item in review["findings"]))

    def test_deterministic_gate_marks_missing_acceptance_signal_yellow(self):
        review = aigile_backend.deterministic_gate_review(
            "Story",
            {"title": "Login", "description": "User should be able to sign in with email and password.", "labels": ["story"]},
        )

        self.assertIsNotNone(review)
        self.assertEqual(review["status"], "yellow")

    def test_valid_agent_json_is_normalized(self):
        review = aigile_backend.normalize_agent_review(
            "QA Engineer Agent",
            {
                "agent_name": "QA Engineer Agent",
                "status": "green",
                "summary": "ok",
                "findings": [],
                "proposed_task_patch": {"acceptance_criteria": ["AC"]},
            },
        )
        self.assertEqual(review["status"], "green")
        self.assertEqual(review["proposed_task_patch"]["acceptance_criteria"], ["AC"])

    def test_invalid_agent_json_becomes_safe_fallback(self):
        review = aigile_backend.normalize_agent_review("QA Engineer Agent", "not-json")
        self.assertEqual(review["status"], "yellow")
        self.assertEqual(review["findings"][0]["title"], "Invalid agent response")

    def test_review_gate_disabled_response(self):
        with mock.patch.object(aigile_backend, "REVIEW_GATE_ENABLED", False):
            result = aigile_backend.run_review_gate({"issue_key": "AIGILE-1"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["disabled"])

    def test_review_gate_blocks_without_type_label_before_agents(self):
        issue_payload = {"id": "1", "key": "AIGILE-1", "title": "Login fails", "type": "Task", "labels": [], "description": ""}
        with (
            mock.patch.object(aigile_backend, "REVIEW_GATE_ENABLED", True),
            mock.patch.object(aigile_backend, "find_issue", return_value=FakeIssue()),
            mock.patch.object(aigile_backend, "issue_to_payload", return_value=issue_payload),
            mock.patch.object(aigile_backend, "review_agent") as review_agent,
            mock.patch.object(aigile_backend, "append_review_history") as history,
            mock.patch.object(aigile_backend, "append_execution_log"),
        ):
            result = aigile_backend.run_review_gate({"issue_key": "AIGILE-1"})

        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertEqual(result["code"], "missing_type_label")
        review_agent.assert_not_called()
        history.assert_not_called()

    def test_review_context_queries_plane_pages_with_agents_and_type(self):
        issue_payload = {
            "key": "AIGILE-10",
            "title": "Registration",
            "description": "User can register",
            "labels": ["Epic"],
        }
        with (
            mock.patch.object(aigile_backend, "read_knowledge_base", return_value="KB"),
            mock.patch.object(aigile_backend, "read_project_pages_context", return_value="STRICT PAGES") as pages_context,
        ):
            context = aigile_backend.build_review_context(issue_payload, "Epic", ["Product Manager Agent", "QA Lead Agent"])

        self.assertIn("KB", context)
        self.assertIn("STRICT PAGES", context)
        query = pages_context.call_args.args[0]
        self.assertIn("AI Review Gate", query)
        self.assertIn("Task type: Epic", query)
        self.assertIn("Epic Template", query)
        self.assertIn("Agent Response Rules", query)
        self.assertIn("Product Manager Agent", query)

    def test_plane_knowledge_templates_include_core_pages(self):
        titles = set(aigile_backend.PLANE_KNOWLEDGE_TEMPLATE_PAGES)
        self.assertIn(aigile_backend.AGENT_RULES_PAGE_TITLE, titles)
        self.assertIn("[AI] Bug Template", titles)
        self.assertIn("[AI] Story Template", titles)
        self.assertIn("[AI] Epic Template", titles)
        self.assertIn("[AI] Agent Response Rules", titles)

    def test_review_history_append_and_read(self):
        tmp = Path(tempfile.gettempdir()) / "aigile-test-review-history.jsonl"
        try:
            with mock.patch.object(aigile_backend, "REVIEW_HISTORY_PATH", tmp):
                aigile_backend.append_review_history({"issue_key": "AIGILE-1", "review_id": "one"})
                aigile_backend.append_review_history({"issue_key": "AIGILE-2", "review_id": "two"})
                reviews = aigile_backend.read_review_history("AIGILE-1")
            self.assertEqual(len(reviews), 1)
            self.assertEqual(reviews[0]["review_id"], "one")
        finally:
            tmp.unlink(missing_ok=True)

    def test_repeated_review_creates_new_review_without_issue_mutation(self):
        issue_payload = {"id": "1", "key": "AIGILE-1", "title": "Story", "type": "Story", "labels": ["type: Story"], "description": ""}
        with (
            mock.patch.object(aigile_backend, "REVIEW_GATE_ENABLED", True),
            mock.patch.object(aigile_backend, "find_issue", return_value=FakeIssue()),
            mock.patch.object(aigile_backend, "issue_to_payload", return_value=issue_payload),
            mock.patch.object(aigile_backend, "read_knowledge_base", return_value="kb"),
            mock.patch.object(aigile_backend, "read_project_pages_context", return_value="pages"),
            mock.patch.object(aigile_backend, "agents_for_issue_type", return_value=["QA Engineer Agent"]),
            mock.patch.object(aigile_backend, "review_agent", return_value=aigile_backend.normalize_agent_review("QA Engineer Agent", {"status": "green"})),
            mock.patch.object(aigile_backend, "append_review_history") as history,
            mock.patch.object(aigile_backend, "append_execution_log"),
        ):
            first = aigile_backend.run_review_gate({"issue_key": "AIGILE-1"})
            second = aigile_backend.run_review_gate({"issue_key": "AIGILE-1"})
        self.assertNotEqual(first["review_id"], second["review_id"])
        self.assertEqual(first["overall_status"], "red")
        self.assertEqual(first["agents"][0]["agent_name"], "AIGILE Review Gate")
        self.assertEqual(history.call_count, 2)

    def test_apply_review_suggestion_adds_comment_and_ai_a_label(self):
        issue = FakeIssueForApply()
        comment = mock.Mock(id="comment-1")
        review = {
            "review_id": "review-1",
            "issue_key": "AIGILE-1",
            "detected_type": "Story",
            "agents": [
                {
                    "agent_name": "QA Engineer Agent",
                    "summary": "Add acceptance criteria.",
                    "findings": [{"title": "Missing AC", "recommendation": "Add criteria"}],
                    "proposed_task_patch": {"acceptance_criteria": ["User can sign in"]},
                }
            ],
        }
        with (
            mock.patch.object(aigile_backend, "REVIEW_GATE_ENABLED", True),
            mock.patch.object(aigile_backend, "find_issue", return_value=issue),
            mock.patch.object(aigile_backend, "issue_to_payload", return_value={"id": "issue-1", "key": "AIGILE-1", "labels": ["type: Story"]}),
            mock.patch.object(aigile_backend, "find_review_history_item", return_value=review),
            mock.patch.object(aigile_backend, "create_issue_comment", return_value=comment) as create_comment,
            mock.patch.object(aigile_backend, "mark_issue_with_ai_label", return_value="AI-A") as mark_label,
            mock.patch.object(aigile_backend, "append_apply_history") as apply_history,
            mock.patch.object(aigile_backend, "append_execution_log"),
        ):
            result = aigile_backend.run_apply_review_suggestion(
                {
                    "issue_key": "AIGILE-1",
                    "review_id": "review-1",
                    "agent_name": "QA Engineer Agent",
                    "finding_index": 0,
                }
            )

        self.assertTrue(result["ok"])
        self.assertFalse(issue.saved)
        self.assertEqual(result["label"], "AI-A")
        create_comment.assert_called_once()
        self.assertIn("AI замечание", create_comment.call_args.args[1])
        self.assertIn("User can sign in", create_comment.call_args.args[1])
        mark_label.assert_called_once_with(issue, "assisted")
        apply_history.assert_called_once()

    def test_red_bug_apply_inserts_russian_bug_template(self):
        issue = FakeIssueForApply()
        comment = mock.Mock(id="comment-1")
        review = {
            "review_id": "review-red",
            "issue_key": "AIGILE-1",
            "detected_type": "Bug",
            "agents": [
                {
                    "agent_name": "QA Engineer Agent",
                    "status": "red",
                    "summary": "Не хватает описания бага.",
                    "findings": [
                        {
                            "severity": "high",
                            "title": "Description is missing",
                            "description": "Нет шагов воспроизведения.",
                            "recommendation": "Заполнить шаблон бага.",
                        }
                    ],
                    "proposed_task_patch": {},
                }
            ],
        }
        with (
            mock.patch.object(aigile_backend, "REVIEW_GATE_ENABLED", True),
            mock.patch.object(aigile_backend, "find_issue", return_value=issue),
            mock.patch.object(aigile_backend, "issue_to_payload", return_value={"id": "issue-1", "key": "AIGILE-1", "labels": ["type: Bug"], "type": "Task"}),
            mock.patch.object(aigile_backend, "find_review_history_item", return_value=review),
            mock.patch.object(aigile_backend, "create_issue_comment", return_value=comment) as create_comment,
            mock.patch.object(aigile_backend, "mark_issue_with_ai_label", return_value="AI-A"),
            mock.patch.object(aigile_backend, "append_apply_history"),
            mock.patch.object(aigile_backend, "append_execution_log"),
        ):
            result = aigile_backend.run_apply_review_suggestion(
                {
                    "issue_key": "AIGILE-1",
                    "review_id": "review-red",
                    "agent_name": "QA Engineer Agent",
                    "finding_index": 0,
                }
            )

        self.assertTrue(result["ok"])
        comment_text = create_comment.call_args.args[1]
        self.assertIn("Шаблон для заполнения бага", comment_text)
        self.assertIn("Текущее поведение", comment_text)
        self.assertIn("Шаги воспроизведения", comment_text)
        self.assertIn("Критерии приемки", comment_text)

    def test_apply_review_suggestion_requires_review_id(self):
        with self.assertRaisesRegex(ValueError, "Missing review_id"):
            aigile_backend.run_apply_review_suggestion({"issue_key": "AIGILE-1"})

    def test_task_chat_message_contains_issue_and_review_context(self):
        issue = {
            "key": "AIGILE-1",
            "title": "Login bug",
            "type": "Bug",
            "labels": ["Bug"],
            "workspace": {"slug": "aigile"},
            "project": {"id": "project-1"},
            "url": "http://localhost:8080/aigile/browse/AIGILE-1",
        }
        graph = {
            "current": issue,
            "parents": [{"key": "AIGILE-0", "title": "Registration epic", "state": "Discovery", "description": "User can register."}],
            "children": [],
            "relations": {"outgoing": [], "incoming": []},
            "cycles": [{"name": "MVP Release Hardening"}],
            "modules": [{"name": "Auth & Identity", "status": "in-progress", "description": "Registration and sessions."}],
            "latest_review": {
                "review_id": "review-1",
                "detected_type": "Bug",
                "overall_status": "red",
                "agents": [{"agent_name": "QA Engineer Agent", "status": "red", "summary": "Missing steps."}],
            },
        }

        message = aigile_backend.format_task_chat_message(graph, "ctx-1")

        self.assertIn("AIGILE-1", message)
        self.assertIn("Login bug", message)
        self.assertIn("QA Engineer Agent", message)
        self.assertIn("Registration epic", message)
        self.assertIn("Auth & Identity", message)
        self.assertIn("ctx-1", message)
        self.assertIn("http://localhost:8080/aigile/browse/AIGILE-1", message)

    def test_task_chat_reply_includes_plane_pages_strict_context(self):
        context = {
            "issue_key": "AIGILE-10",
            "issue": {"key": "AIGILE-10", "title": "Registration", "type": "Bug"},
        }
        graph = {
            "current": {"key": "AIGILE-10", "title": "Registration", "type": "Bug"},
            "parents": [],
            "children": [],
            "relations": {},
            "cycles": [],
            "modules": [],
            "latest_review": {},
        }
        captured = {}

        def fake_ollama(messages, options=None, timeout=180):
            captured["prompt"] = messages[-1]["content"]
            return {"message": {"content": "Ответ"}}

        with (
            mock.patch.object(aigile_backend, "load_fresh_task_graph", return_value=graph),
            mock.patch.object(aigile_backend, "read_project_pages_context", return_value="STRICT TASK CHAT RULES") as pages_context,
            mock.patch.object(aigile_backend, "ollama_chat_completion", side_effect=fake_ollama),
        ):
            answer = aigile_backend.generate_task_chat_reply(context, "помоги оформить AC", "User: previous")

        self.assertEqual(answer, "Ответ")
        self.assertIn("STRICT TASK CHAT RULES", captured["prompt"])
        query = pages_context.call_args.args[0]
        self.assertIn("Task Chat Agent", query)
        self.assertIn("Bug Template", query)
        self.assertIn("Agent Response Rules", query)
        self.assertIn("Acceptance Criteria", query)

    def test_start_task_chat_sends_dm_and_saves_context(self):
        issue_payload = {
            "id": "issue-1",
            "key": "AIGILE-1",
            "title": "Login bug",
            "type": "Bug",
            "labels": ["Bug"],
            "workspace": {"slug": "aigile"},
            "project": {"id": "project-1"},
        }
        review = {"review_id": "review-1", "detected_type": "Bug", "overall_status": "yellow", "agents": []}
        with (
            mock.patch.object(aigile_backend, "find_issue", return_value=FakeIssue()),
            mock.patch.object(aigile_backend, "issue_to_payload", return_value=issue_payload),
            mock.patch.object(
                aigile_backend,
                "build_issue_context_graph",
                return_value={
                    "current": issue_payload,
                    "parents": [],
                    "children": [],
                    "relations": {"outgoing": [], "incoming": []},
                    "cycles": [],
                    "modules": [],
                    "latest_review": review,
                },
            ),
            mock.patch.object(aigile_backend, "resolve_mattermost_user", return_value={"id": "user-1"}),
            mock.patch.object(aigile_backend, "create_direct_channel", return_value={"id": "channel-1"}),
            mock.patch.object(aigile_backend, "post_mattermost_channel_message", return_value={"id": "post-1"}) as post_message,
            mock.patch.object(aigile_backend, "append_task_chat_context") as context_log,
            mock.patch.object(aigile_backend, "mark_task_chat_thread_started") as mark_thread,
            mock.patch.object(aigile_backend, "append_execution_log"),
        ):
            result = aigile_backend.run_start_task_chat({"issue_key": "AIGILE-1", "review_id": "review-1"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["channel_id"], "channel-1")
        self.assertEqual(result["thread_root_id"], "post-1")
        post_message.assert_called_once()
        context_log.assert_called_once()
        mark_thread.assert_called_once_with("post-1", result["context_id"])

    def test_task_chat_poll_replies_in_existing_thread(self):
        context = {
            "ok": True,
            "context_id": "ctx-1",
            "issue_key": "AIGILE-1",
            "channel_id": "channel-1",
            "post_id": "root-1",
            "thread_root_id": "root-1",
            "issue": {"key": "AIGILE-1", "title": "Login bug"},
            "context_graph": {"current": {"key": "AIGILE-1", "title": "Login bug"}},
        }
        posts = [
            {"id": "root-1", "user_id": "bot-1", "message": "Task card", "create_at": 1},
            {"id": "user-reply-1", "user_id": "user-1", "message": "Что в родительском эпике?", "create_at": 2},
        ]
        with (
            mock.patch.object(aigile_backend, "read_task_chat_contexts", return_value=[context]),
            mock.patch.object(aigile_backend, "mattermost_current_user", return_value={"id": "bot-1"}),
            mock.patch.object(aigile_backend, "read_task_chat_state", return_value={"threads": {"root-1": {"processed_post_ids": ["root-1"]}}}),
            mock.patch.object(aigile_backend, "mattermost_thread_posts", return_value=posts),
            mock.patch.object(aigile_backend, "generate_task_chat_reply", return_value="Ответ по задаче"),
            mock.patch.object(aigile_backend, "post_mattermost_channel_message", return_value={"id": "bot-reply-1"}) as post_message,
            mock.patch.object(aigile_backend, "write_task_chat_state") as write_state,
            mock.patch.object(aigile_backend, "append_execution_log"),
        ):
            result = aigile_backend.poll_task_chat_threads({"force": True})

        self.assertTrue(result["ok"])
        self.assertEqual(result["replies"], 1)
        post_message.assert_called_once_with("channel-1", "Ответ по задаче", root_id="root-1")
        write_state.assert_called_once()


    def test_task_chat_update_request_creates_pending_draft(self):
        context = {
            "ok": True,
            "context_id": "ctx-1",
            "issue_key": "AIGILE-1",
            "channel_id": "channel-1",
            "thread_root_id": "root-1",
            "issue": {"key": "AIGILE-1", "title": "Login bug"},
            "context_graph": {"current": {"key": "AIGILE-1", "title": "Login bug"}},
        }
        state = {"threads": {"root-1": {"processed_post_ids": ["root-1"]}}}
        posts = [
            {"id": "root-1", "user_id": "bot-1", "message": "Task card", "create_at": 1},
            {"id": "user-reply-1", "user_id": "user-1", "message": "!ac пользователь видит понятную ошибку при слабом пароле", "create_at": 2},
        ]
        with (
            mock.patch.object(aigile_backend, "read_task_chat_contexts", return_value=[context]),
            mock.patch.object(aigile_backend, "mattermost_current_user", return_value={"id": "bot-1"}),
            mock.patch.object(aigile_backend, "read_task_chat_state", return_value=state),
            mock.patch.object(aigile_backend, "mattermost_thread_posts", return_value=posts),
            mock.patch.object(aigile_backend, "post_mattermost_channel_message", return_value={"id": "bot-reply-1"}) as post_message,
            mock.patch.object(aigile_backend, "write_task_chat_state") as write_state,
            mock.patch.object(aigile_backend, "append_execution_log"),
        ):
            result = aigile_backend.poll_task_chat_threads({"force": True})

        self.assertTrue(result["ok"])
        self.assertEqual(result["replies"], 1)
        self.assertIn("pending_draft", state["threads"]["root-1"])
        self.assertIn("Черновик изменения готов", post_message.call_args.args[1])
        write_state.assert_called_once()

    def test_task_chat_help_replies_without_creating_draft(self):
        context = {
            "ok": True,
            "context_id": "ctx-1",
            "issue_key": "AIGILE-1",
            "channel_id": "channel-1",
            "thread_root_id": "root-1",
            "issue": {"key": "AIGILE-1", "title": "Login bug"},
            "context_graph": {"current": {"key": "AIGILE-1", "title": "Login bug"}},
        }
        state = {"threads": {"root-1": {"processed_post_ids": ["root-1"]}}}
        posts = [
            {"id": "root-1", "user_id": "bot-1", "message": "Task card", "create_at": 1},
            {"id": "user-reply-1", "user_id": "user-1", "message": "!help", "create_at": 2},
        ]
        with (
            mock.patch.object(aigile_backend, "read_task_chat_contexts", return_value=[context]),
            mock.patch.object(aigile_backend, "mattermost_current_user", return_value={"id": "bot-1"}),
            mock.patch.object(aigile_backend, "read_task_chat_state", return_value=state),
            mock.patch.object(aigile_backend, "mattermost_thread_posts", return_value=posts),
            mock.patch.object(aigile_backend, "post_mattermost_channel_message", return_value={"id": "bot-reply-1"}) as post_message,
            mock.patch.object(aigile_backend, "write_task_chat_state"),
            mock.patch.object(aigile_backend, "append_execution_log"),
        ):
            result = aigile_backend.poll_task_chat_threads({"force": True})

        self.assertTrue(result["ok"])
        self.assertNotIn("pending_draft", state["threads"]["root-1"])
        self.assertIn("AIGILE Task Agent", post_message.call_args.args[1])
        self.assertIn("!status", post_message.call_args.args[1])

    def test_task_chat_status_shows_memory_without_creating_draft(self):
        context = {
            "ok": True,
            "context_id": "ctx-1",
            "issue_key": "AIGILE-1",
            "channel_id": "channel-1",
            "thread_root_id": "root-1",
            "issue": {"key": "AIGILE-1", "title": "Login bug"},
            "context_graph": {
                "current": {"key": "AIGILE-1", "title": "Login bug", "type": "Bug"},
                "parent_chain": [{"key": "AIGILE-10"}],
                "children": [{"key": "AIGILE-2"}],
                "relations": {"outgoing": [{"key": "AIGILE-3"}], "incoming": []},
                "modules": [{"name": "Auth"}],
                "cycles": [{"name": "Sprint"}],
                "latest_review": {"overall_status": "yellow"},
            },
        }
        state = {"threads": {"root-1": {"processed_post_ids": ["root-1"], "dialogue_history": [{"role": "user", "message": "previous"}]}}}
        posts = [
            {"id": "root-1", "user_id": "bot-1", "message": "Task card", "create_at": 1},
            {"id": "user-reply-1", "user_id": "user-1", "message": "!status", "create_at": 2},
        ]
        with (
            mock.patch.object(aigile_backend, "read_task_chat_contexts", return_value=[context]),
            mock.patch.object(aigile_backend, "mattermost_current_user", return_value={"id": "bot-1"}),
            mock.patch.object(aigile_backend, "read_task_chat_state", return_value=state),
            mock.patch.object(aigile_backend, "mattermost_thread_posts", return_value=posts),
            mock.patch.object(aigile_backend, "post_mattermost_channel_message", return_value={"id": "bot-reply-1"}) as post_message,
            mock.patch.object(aigile_backend, "write_task_chat_state"),
            mock.patch.object(aigile_backend, "append_execution_log"),
        ):
            result = aigile_backend.poll_task_chat_threads({"force": True})

        self.assertTrue(result["ok"])
        self.assertNotIn("pending_draft", state["threads"]["root-1"])
        self.assertIn("AIGILE-1", post_message.call_args.args[1])
        self.assertIn("yellow", post_message.call_args.args[1])

    def test_task_chat_detects_acceptance_criteria_without_prefix(self):
        command = aigile_backend.detect_task_chat_command("сформируй Acceptance criteria чтобы мы это не забыли")

        self.assertEqual(command["command"], "!ac")
        self.assertEqual(command["change_kind"], "acceptance criteria")

    def test_task_chat_detects_quick_update_commands(self):
        cases = {
            "!risk auth can block release": ("!risk", "risk note"),
            "!dep depends on backend endpoint": ("!dep", "dependency note"),
            "!deadline finish before June 16": ("!deadline", "deadline note"),
        }

        for message, expected in cases.items():
            with self.subTest(message=message):
                command = aigile_backend.detect_task_chat_command(message)
                self.assertEqual(command["command"], expected[0])
                self.assertEqual(command["change_kind"], expected[1])

    def test_task_chat_help_and_status_detection(self):
        self.assertTrue(aigile_backend.is_task_chat_help("!help"))
        self.assertTrue(aigile_backend.is_task_chat_status("!status"))
        self.assertFalse(aigile_backend.looks_like_task_update_request("!help"))
        self.assertFalse(aigile_backend.looks_like_task_update_request("!status"))

    def test_plane_page_rag_requires_ai_marker_and_public_access(self):
        public_page = SimpleNamespace(name="[AI] Agent Rules", access=0, deleted_at=None, archived_at=None)
        private_page = SimpleNamespace(name="[AI] Private Rules", access=1, deleted_at=None, archived_at=None)
        ordinary_page = SimpleNamespace(name="Agent Rules", access=0, deleted_at=None, archived_at=None)

        self.assertTrue(aigile_backend.is_plane_page_approved_for_rag(public_page))
        self.assertFalse(aigile_backend.is_plane_page_approved_for_rag(private_page))
        self.assertFalse(aigile_backend.is_plane_page_approved_for_rag(ordinary_page))

    def test_plane_page_source_path_uses_project_identifier_and_page_id(self):
        project = mock.Mock(identifier="AIGILE")
        page = mock.Mock(id="page-1")

        self.assertEqual(aigile_backend.plane_page_source_path(project, page), "plane_pages/AIGILE/page-1.md")

    def test_task_chat_resolves_add_them_to_previous_agent_acceptance_criteria(self):
        context = {
            "issue_key": "AIGILE-1",
            "issue": {"key": "AIGILE-1", "title": "Login bug"},
            "context_graph": {"current": {"key": "AIGILE-1", "title": "Login bug"}},
        }
        history = """
AI Agent: Конечно. [AI] Проверка существования аккаунта: - Пользователь не может зарегистрироваться с уже существующим email. [AI] Обработка ошибок безопасности: - Валидация формата email.
User: добавь их в задачу
"""
        with mock.patch.object(aigile_backend, "load_fresh_task_graph", return_value=context["context_graph"]):
            draft = aigile_backend.build_task_update_draft(context, "добавь их в задачу", history)

        self.assertEqual(draft["command"]["command"], "!ac")
        self.assertTrue(draft["resolved_from_thread"])
        self.assertIn("Проверка существования аккаунта", draft["proposed_text"])
        self.assertIn("Обработка ошибок безопасности", draft["proposed_text"])

    def test_task_chat_acceptance_approval_updates_description_and_aia_label(self):
        issue = FakeIssueForApply()
        issue.description_stripped = "Existing description"
        comment = mock.Mock(id="comment-1")
        draft = {
            "draft_id": "draft-1",
            "issue_key": "AIGILE-1",
            "issue_title": "Login bug",
            "requested_message": "!ac user sees a clear weak password error",
            "command": {
                "command": "!ac",
                "section_title": "Acceptance criteria",
                "content": "user sees a clear weak password error",
            },
        }
        context = {"issue_key": "AIGILE-1"}

        with (
            mock.patch.object(aigile_backend, "find_issue", return_value=issue),
            mock.patch.object(aigile_backend, "create_issue_comment", return_value=comment) as create_comment,
            mock.patch.object(aigile_backend, "mark_issue_with_ai_label", return_value="AIA") as mark_label,
            mock.patch.object(aigile_backend, "append_apply_history") as apply_history,
            mock.patch.object(aigile_backend, "append_execution_log"),
        ):
            result = aigile_backend.apply_task_update_draft(context, draft, approved_by="user-1")

        self.assertTrue(result["ok"])
        self.assertTrue(result["description_updated"])
        self.assertEqual(result["label"], "AIA")
        self.assertTrue(issue.saved)
        self.assertIn("## Acceptance Criteria", issue.description_stripped)
        self.assertIn("- [AI] user sees a clear weak password error", issue.description_stripped)
        mark_label.assert_called_once_with(issue, "agent_assisted")
        comment_text = create_comment.call_args.args[1]
        self.assertIn("Пользователь:", comment_text)
        self.assertIn("Запрос:", comment_text)
        self.assertIn("Изменение:", comment_text)
        apply_history.assert_called_once()

    def test_task_chat_acceptance_approval_adds_multiple_ai_lines(self):
        updated, changed = aigile_backend.add_ai_acceptance_criteria(
            "Existing description",
            "first accepted behavior\nsecond accepted behavior",
        )

        self.assertTrue(changed)
        self.assertIn("- [AI] first accepted behavior", updated)
        self.assertIn("- [AI] second accepted behavior", updated)

    def test_task_chat_approval_applies_pending_draft(self):
        context = {
            "ok": True,
            "context_id": "ctx-1",
            "issue_key": "AIGILE-1",
            "channel_id": "channel-1",
            "thread_root_id": "root-1",
            "issue": {"key": "AIGILE-1", "title": "Login bug"},
        }
        draft = {
            "draft_id": "draft-1",
            "issue_key": "AIGILE-1",
            "issue_title": "Login bug",
            "requested_message": "добавь acceptance criteria",
            "comment_markdown": "approved comment",
        }
        state = {"threads": {"root-1": {"processed_post_ids": ["root-1"], "pending_draft": draft}}}
        posts = [
            {"id": "root-1", "user_id": "bot-1", "message": "Task card", "create_at": 1},
            {"id": "approve-1", "user_id": "user-1", "message": "y", "create_at": 2},
        ]
        with (
            mock.patch.object(aigile_backend, "read_task_chat_contexts", return_value=[context]),
            mock.patch.object(aigile_backend, "mattermost_current_user", return_value={"id": "bot-1"}),
            mock.patch.object(aigile_backend, "read_task_chat_state", return_value=state),
            mock.patch.object(aigile_backend, "mattermost_thread_posts", return_value=posts),
            mock.patch.object(
                aigile_backend,
                "apply_task_update_draft",
                return_value={"issue_key": "AIGILE-1", "label": "AI-A"},
            ) as apply_draft,
            mock.patch.object(aigile_backend, "post_mattermost_channel_message", return_value={"id": "bot-reply-1"}) as post_message,
            mock.patch.object(aigile_backend, "write_task_chat_state") as write_state,
            mock.patch.object(aigile_backend, "append_execution_log"),
        ):
            result = aigile_backend.poll_task_chat_threads({"force": True})

        self.assertTrue(result["ok"])
        self.assertEqual(result["replies"], 1)
        self.assertNotIn("pending_draft", state["threads"]["root-1"])
        apply_draft.assert_called_once()
        self.assertIn("Готово", post_message.call_args.args[1])
        write_state.assert_called_once()


if __name__ == "__main__":
    unittest.main()
