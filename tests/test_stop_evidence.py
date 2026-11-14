"""Synthetic .167 transcript shapes for bounded Stop evidence."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skill/goal-await/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
CHAT = "11111111-1111-1111-1111-111111111111"
CHILD = "22222222-2222-2222-2222-222222222222"
TURN = "33333333-3333-3333-3333-333333333333"
MODEL = "fixture/model"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


evidence = _load("recovery_evidence", "recovery_evidence.py")
stop_evidence = _load("stop_evidence", "stop_evidence.py")


class StopEvidenceTests(unittest.TestCase):
    def setUp(self):
        work = REPO / ".test-work"
        work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.transcript = self.root / "sessions" / ("rollout-" + CHAT + ".jsonl")
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text(
            self.json_line(dict(type="session_meta", payload=dict(
                id=CHAT, cwd=str(self.workspace), cli_version="0.167.0-alpha.3.2"))),
            encoding="utf-8")
        self.ordinal = 0
        self.state = self.make_state()
        self.event = dict(hook_event_name="Stop", session_id=CHAT,
                          turn_id=TURN, cwd=str(self.workspace))

    def reset_transcript(self):
        self.transcript.write_text(
            self.json_line(dict(type="session_meta", payload=dict(
                id=CHAT, cwd=str(self.workspace), cli_version="0.167.0-alpha.3.2"))),
            encoding="utf-8")
        self.ordinal = 0

    def make_state(self):
        return dict(state="RUNNING", thread_id=CHAT, workspace=str(self.workspace),
                    recovery=dict(snapshot=dict(
                        model=MODEL, transcript_path=str(self.transcript))))

    def json_line(self, value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"

    def append(self, outer_type, **payload):
        self.ordinal += 1
        self.append_raw(dict(timestamp=f"2026-11-07T01:00:{self.ordinal:02d}Z",
                             ordinal=self.ordinal, type=outer_type, payload=payload))

    def append_raw(self, record):
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(self.json_line(record))

    def start(self, turn=TURN, trigger="composer", mode="default"):
        self.append("event_msg", type="task_started", turn_id=turn,
                    turn_attribution=dict(turn_id=turn, turn_trigger=trigger))
        self.append("turn_context", turn_id=turn, cwd=str(self.workspace),
                    model=MODEL, collaboration_mode=dict(mode=mode, settings={}))

    def item(self, kind, item_id, status="completed", event="item_completed",
             turn=TURN, **fields):
        self.append("event_msg", type=event, thread_id=CHAT, turn_id=turn,
                    item=dict(type=kind, id=item_id, status=status, **fields))

    def text(self, text="final answer", phase="final_answer"):
        self.item("AgentMessage", "msg_answer", content=[dict(
            type="output_text", text=text)], phase=phase)
        self.append("event_msg", type="item_completed", thread_id=CHAT,
                    turn_id=TURN, item=dict(type="HookPrompt", id="msg_hook",
                                            fragments=[dict(hookRunId="stop:0", text="quiet")]))

    def exec_call(self, call_id="exec_1_aa", has_output=False):
        self.append("response_item", type="custom_tool_call", id="ctc_" + call_id,
                    status="completed", call_id=call_id, name="exec", input="text(1)",
                    internal_chat_message_metadata_passthrough=dict(turn_id=TURN))
        if has_output:
            self.exec_output(call_id)

    def exec_output(self, call_id="exec_1_aa"):
        self.append("response_item", type="custom_tool_call_output", id="ctco_" + call_id,
                    call_id=call_id, output=[dict(type="input_text", text="done")],
                    internal_chat_message_metadata_passthrough=dict(turn_id=TURN))

    def notification(self, child=CHILD):
        body = json.dumps(dict(agent_path=child, status=dict(completed="DONE")))
        self.append("response_item", type="message", role="user",
                    content=[dict(type="input_text", text=
                                  "<subagent_notification>" + body + "</subagent_notification>")],
                    internal_chat_message_metadata_passthrough=dict(
                        turn_id=TURN,
                        content_item_kinds=["multi_agent.subagent_notification"],
                        content_item_metadata=[dict(provenance=dict(
                            type="tool", namespace="multi_agent_v1"))]))

    def assess(self, previous=None):
        return stop_evidence.assess(self.event, self.state, previous)

    def test_initial_text_only_is_no_activity(self):
        self.start()
        self.text("I inspected the request and will explain the approach.")
        result = self.assess({})
        self.assertEqual(result["kind"], "no_activity")
        self.assertEqual(result["activity_count"], 0)
        self.assertFalse(result["automatic"])
        self.assertRegex(result["cursor"], r"^[0-9a-f]{64}$")

    def test_heartbeat_turn_is_marked_automatic(self):
        self.start(trigger="automation_heartbeat_scheduled")
        self.text("Heartbeat returned without a tool.")
        result = self.assess({})
        self.assertEqual(result["kind"], "no_activity")
        self.assertTrue(result["automatic"])

    def test_terminal_business_tools_count_even_when_failed(self):
        self.start()
        self.item("CommandExecution", "exec-1", status="failed", exit_code=1,
                  command=[sys.executable, "inspect.py"])
        self.item("FileChange", "change-1", changes=[dict(path="result.txt")])
        self.item("McpToolCall", "mcp-1", status="failed", tool="search",
                  server="fixture", result=dict(isError=True))
        result = self.assess()
        self.assertEqual(result["kind"], "activity")
        self.assertEqual(result["activity_count"], 3)

    def test_goal_and_plan_controls_do_not_count_or_leave_pending(self):
        self.start()
        self.item("CommandExecution", "exec-control", status="completed", exit_code=0,
                  command=[sys.executable, str(SCRIPTS / "goal_entry.py"), "status"])
        self.item("McpToolCall", "mcp-control", tool="automation_update", server="codex_app")
        self.item("McpToolCall", "mcp-plan", tool="update_plan", server="codex_app")
        self.exec_call(has_output=True)
        result = self.assess()
        self.assertEqual(result["kind"], "no_activity")
        self.assertEqual(result["activity_count"], 0)

    def test_pending_control_waits_then_completes_without_activity(self):
        self.start()
        self.item("McpToolCall", "automation-control", status="in_progress",
                  event="item_started", tool="automation_update", server="codex_app")
        pending = self.assess({})
        self.assertEqual(pending["kind"], "waiting")
        self.assertEqual(pending["activity_count"], 0)
        self.item("McpToolCall", "automation-control", status="completed",
                  tool="automation_update", server="codex_app")
        completed = self.assess(pending)
        self.assertEqual(completed["kind"], "no_activity")
        self.assertEqual(completed["activity_count"], 0)

    def test_next_segment_excludes_prior_tools(self):
        self.start()
        self.item("CommandExecution", "exec-1", command=[sys.executable, "inspect.py"])
        first = self.assess()
        self.assertEqual(first["activity_count"], 1)
        self.item("CommandExecution", "exec-control", exit_code=0,
                  command=[sys.executable, str(SCRIPTS / "goal_await.py"), "status"])
        second = self.assess(first)
        self.assertEqual(second["kind"], "no_activity")
        self.assertEqual(second["activity_count"], 0)
        self.assertNotEqual(first["cursor"], second["cursor"])

    def test_outer_exec_is_pending_until_output_and_never_activity(self):
        self.start()
        self.exec_call()
        pending = self.assess()
        self.assertEqual(pending["kind"], "waiting")
        self.item("CommandExecution", "exec-child", status="failed", exit_code=1,
                  command=[sys.executable, "inspect.py"])
        self.exec_output()
        resolved = self.assess(pending)
        self.assertEqual(resolved["kind"], "activity")
        self.assertEqual(resolved["activity_count"], 1)

    def test_empty_collab_wait_keeps_child_pending_until_notification(self):
        self.start()
        self.item("CollabAgentToolCall", "collab-spawn", tool="spawn_agent",
                  receiver_thread_ids=[CHILD], agents_states={CHILD: "pending_init"})
        first = self.assess()
        self.assertEqual(first["kind"], "waiting")
        self.item("CollabAgentToolCall", "collab-wait", tool="wait",
                  receiver_thread_ids=[], agents_states={})
        still = self.assess(first)
        self.assertEqual(still["kind"], "waiting")
        self.notification()
        finished = self.assess(still)
        self.assertEqual(finished["kind"], "activity")
        self.assertEqual(finished["activity_count"], 1)

    def test_terminal_collab_wait_resolves_a_running_child(self):
        self.start()
        self.item("CollabAgentToolCall", "collab-send", tool="send_input",
                  receiver_thread_ids=[CHILD], agents_states={CHILD: "running"})
        first = self.assess()
        self.assertEqual(first["kind"], "waiting")
        self.item("CollabAgentToolCall", "collab-wait", tool="wait",
                  receiver_thread_ids=[CHILD],
                  agents_states={CHILD: dict(completed="DONE")})
        result = self.assess(first)
        self.assertEqual(result["kind"], "activity")
        self.assertEqual(result["activity_count"], 1)

    def test_errored_collab_child_report_is_terminal_activity(self):
        self.start()
        self.item("CollabAgentToolCall", "collab-send", tool="send_input",
                  receiver_thread_ids=[CHILD], agents_states={CHILD: "running"})
        first = self.assess({})
        self.assertEqual(first["kind"], "waiting")
        self.item("CollabAgentToolCall", "collab-wait", tool="wait_agent",
                  receiver_thread_ids=[CHILD],
                  agents_states={CHILD: dict(errored="FAILED: inspector failure")})
        result = self.assess(first)
        self.assertEqual(result["kind"], "activity")
        self.assertEqual(result["activity_count"], 1)

    def test_plan_and_interrupt_are_quiet(self):
        self.start(mode="plan")
        self.item("CommandExecution", "exec-plan", command=[sys.executable, "inspect.py"])
        plan = self.assess()
        self.assertEqual(plan["kind"], "plan")
        self.assertFalse(plan["automatic"])

        self.reset_transcript()
        self.start()
        self.append("event_msg", type="turn_aborted", turn_id=TURN, reason="cancelled")
        interrupted = self.assess()
        self.assertEqual(interrupted["kind"], "interrupted")
        self.assertFalse(interrupted["automatic"])

    def test_foreign_metadata_and_turn_mismatches_fail_closed(self):
        self.start()
        self.state["thread_id"] = "44444444-4444-4444-4444-444444444444"
        self.assertEqual(self.assess()["kind"], "unknown")

        self.state = self.make_state()
        self.start("99999999-9999-9999-9999-999999999999")
        self.assertEqual(self.assess()["kind"], "unknown")

    def test_model_changes_and_absent_model_do_not_hide_activity(self):
        for recorded_model, current_model in [(MODEL, "fallback/model"),
                                               (None, MODEL), (MODEL, None)]:
            with self.subTest(recorded_model=recorded_model, current_model=current_model):
                self.reset_transcript()
                self.state = self.make_state()
                if recorded_model is None:
                    self.state["recovery"]["snapshot"].pop("model")
                self.start()
                context = dict(turn_id=TURN, cwd=str(self.workspace))
                if current_model is not None:
                    context["model"] = current_model
                self.append("turn_context", **context)
                self.item("CommandExecution", "verify", command=["python", "verify.py"], exit_code=0)
                self.assertEqual(self.assess()["kind"], "activity")

    def test_partial_tail_and_missing_cursor_are_unknown(self):
        self.start()
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write('{"timestamp":"2026-11-07T02:00:00Z","type":')
        self.assertEqual(self.assess()["kind"], "unknown")

        self.reset_transcript()
        self.start()
        missing = self.assess(dict(turn_id=TURN, cursor="f" * 64))
        self.assertEqual(missing["kind"], "unknown")

    def test_bounded_tail_without_required_cursor_is_unknown(self):
        self.start()
        for index in range(120):
            self.item("Reasoning", "reason-%s" % index)
        with mock.patch.object(evidence, "MAX_TAIL_BYTES", 700):
            result = self.assess()
        self.assertEqual(result["kind"], "unknown")

    def test_guardian_review_tools_are_isolated_from_main_turn(self):
        review = "55555555-5555-5555-5555-555555555555"
        self.start()
        self.start(review, "guardian_review")
        self.item("CommandExecution", "review-tool", turn=review,
                  command=[sys.executable, "review.py"])
        self.item("CommandExecution", "main-tool", command=[sys.executable, "inspect.py"])
        result = self.assess()
        self.assertEqual(result["kind"], "activity")
        self.assertEqual(result["activity_count"], 1)

    def test_live_wait_handle_is_waiting(self):
        self.start()
        self.state.update(state="WAITING", automation_id="timer-1", wait=dict(
            waiting_for="result", completion="ready", result_location="result.json"))
        result = self.assess()
        self.assertEqual(result["kind"], "waiting")
        self.assertFalse(result["automatic"])

    def test_legacy_same_turn_without_cursor_starts_after_prior_hook(self):
        self.start()
        self.item("CommandExecution", "old-tool", command=[sys.executable, "inspect.py"])
        self.append("event_msg", type="item_completed", thread_id=CHAT,
                    turn_id=TURN, item=dict(type="HookPrompt", id="old-hook",
                        fragments=[dict(hookRunId="stop:0", text="old feedback")]))
        self.text("New answer only; no further tool ran.")
        result = self.assess(dict(turn_id=TURN, generation=0, continuations=1))
        self.assertEqual(result["kind"], "no_activity")
        self.assertEqual(result["activity_count"], 0)

    def test_legacy_boundary_ignores_non_stop_hook_prompts(self):
        self.start()
        self.append("event_msg", type="item_completed", thread_id=CHAT,
                    turn_id=TURN, item=dict(type="HookPrompt", id="stop-hook",
                        fragments=[dict(hookRunId="stop:0:hooks.json", text="feedback")]))
        self.item("CommandExecution", "business-after-stop",
                  command=[sys.executable, "inspect.py"])
        self.append("event_msg", type="item_completed", thread_id=CHAT,
                    turn_id=TURN, item=dict(type="HookPrompt", id="pre-hook",
                        fragments=[dict(hookRunId="pretooluse:0:hooks.json",
                                        text="permission feedback")]))
        result = self.assess(dict(turn_id=TURN, generation=0, continuations=1))
        self.assertEqual(result["kind"], "activity")
        self.assertEqual(result["activity_count"], 1)

    def test_legacy_without_hook_does_not_reclassify_old_tools(self):
        self.start()
        self.item("CommandExecution", "old-tool", command=[sys.executable, "inspect.py"])
        result = self.assess(dict(turn_id=TURN, generation=0, continuations=1))
        self.assertEqual(result["kind"], "no_activity")
        self.assertEqual(result["activity_count"], 0)

    def test_terminal_notification_string_and_error_statuses_clear_children(self):
        other = "66666666-6666-6666-6666-666666666666"
        self.start()
        self.item("CollabAgentToolCall", "collab-many", tool="send_input",
                  receiver_thread_ids=[CHILD, other],
                  agents_states={CHILD: "running", other: "running"})
        first = self.assess({})
        self.assertEqual(first["kind"], "waiting")
        body = json.dumps(dict(agent_path=CHILD, status="interrupted"))
        self.append("response_item", type="message", role="user",
                    content=[dict(type="input_text", text=
                                  "<subagent_notification>" + body + "</subagent_notification>")],
                    internal_chat_message_metadata_passthrough=dict(
                        turn_id=TURN, content_item_kinds=["multi_agent.subagent_notification"],
                        content_item_metadata=[dict(provenance=dict(
                            type="tool", namespace="multi_agent_v1"))]))
        body = json.dumps(dict(agent_path=other, status=dict(errored="FAILED")))
        self.append("response_item", type="message", role="user",
                    content=[dict(type="input_text", text=
                                  "<subagent_notification>" + body + "</subagent_notification>")],
                    internal_chat_message_metadata_passthrough=dict(
                        turn_id=TURN, content_item_kinds=["multi_agent.subagent_notification"],
                        content_item_metadata=[dict(provenance=dict(
                            type="tool", namespace="multi_agent_v1"))]))
        result = self.assess(first)
        self.assertEqual(result["kind"], "activity")
        self.assertEqual(result["activity_count"], 2)

    def test_response_function_fallback_deduplicates_outputs(self):
        self.start()
        self.append("response_item", type="function_call", call_id="call-1",
                    name="read_thread", arguments="{}")
        self.append("response_item", type="function_call_output", call_id="call-1",
                    output="done")
        self.append("response_item", type="function_call_output", call_id="call-1",
                    output="done again")
        result = self.assess()
        self.assertEqual(result["kind"], "activity")
        self.assertEqual(result["activity_count"], 1)

    def test_shell_transport_output_does_not_count_without_nested_activity(self):
        self.start()
        self.append("response_item", type="function_call", call_id="call-shell",
                    name="functions.exec_command",
                    arguments=json.dumps(dict(cmd="python goal_entry.py status")))
        self.item("CommandExecution", "control-command",
                  command=[sys.executable, str(SCRIPTS / "goal_entry.py"), "status"])
        self.append("response_item", type="function_call_output", call_id="call-shell",
                    output="status complete")
        result = self.assess({})
        self.assertEqual(result["kind"], "no_activity")
        self.assertEqual(result["activity_count"], 0)

    def test_exact_chat_is_located_when_snapshot_has_no_path(self):
        self.start()
        self.state["recovery"]["snapshot"].pop("transcript_path")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.root)}):
            result = self.assess()
        self.assertEqual(result["kind"], "no_activity")


if __name__ == "__main__":
    unittest.main()
