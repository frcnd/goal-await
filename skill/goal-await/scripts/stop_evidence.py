"""Bounded Stop-time evidence from one exact Codex transcript.

The assessor returns counts and a cursor only.  It never returns or stores tool
arguments, command output, prompt text, or notification bodies.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

import recovery_evidence as evidence


_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_PENDING = {
    "pending", "pending_init", "pendinginit", "queued", "started",
    "in_progress", "inprogress", "running", "waiting",
}
_TERMINAL = {
    "cancelled", "canceled", "closed", "complete", "completed", "done",
    "error", "errored", "failed", "interrupted", "success", "succeeded",
}
_CONTROL = {
    "automationupdate", "createautomation", "deleteautomation",
    "goalawait", "goalcontext", "goaleffects", "goalentry", "goalstatus",
    "pauseautomation",
    "updateautomation", "updateplan",
}
_TOOL_ITEMS = {
    "collabagenttoolcall", "commandexecution", "dynamictoolcall",
    "dynamictoolresult", "filechange", "mcptoolcall", "toolcall",
    "toolresult",
}
_AUTOMATIC_TRIGGERS = {"automation_heartbeat_scheduled", "automation_heartbeat_run_now"}


def _result(kind: str, reason: str, turn_id: str, cursor: str,
            automatic: bool, activity_count: int) -> dict[str, Any]:
    return {
        "kind": kind,
        "reason": reason,
        "turn_id": turn_id,
        "cursor": cursor if isinstance(cursor, str) else "",
        "automatic": bool(automatic),
        "activity_count": int(activity_count),
    }


def _unknown(reason: str, turn_id: str, cursor: Any = "") -> dict[str, Any]:
    return _result("unknown", reason, turn_id, cursor, False, 0)


def _cursor(record: dict[str, Any]) -> str:
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("payload")
    return value if isinstance(value, dict) else {}


def _turn(payload: dict[str, Any]) -> str | None:
    value = payload.get("turn_id")
    if isinstance(value, str) and value:
        return value
    meta = payload.get("internal_chat_message_metadata_passthrough")
    value = meta.get("turn_id") if isinstance(meta, dict) else None
    return value if isinstance(value, str) and value else None


def _status(item: dict[str, Any]) -> str:
    value = item.get("status")
    return value.strip().casefold() if isinstance(value, str) else ""


def _kind(item: dict[str, Any]) -> str:
    value = item.get("type")
    return re.sub(r"[^a-z]", "", value.casefold()) if isinstance(value, str) else ""


def _name(item: dict[str, Any]) -> str:
    for key in ("tool", "name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    return ""


def _control_name(value: str) -> bool:
    tail = re.split(r"[.:/\\]", value.casefold())[-1]
    return re.sub(r"[^a-z]", "", tail) in _CONTROL


def _transport_name(value: str) -> bool:
    tail = re.split(r"[.:/\\]", value.casefold())[-1]
    return re.sub(r"[^a-z]", "", tail) in {
        "exec", "execcommand", "writestdin",
    }


def _goal_command(item: dict[str, Any]) -> bool:
    command = item.get("command")
    parts = [str(value) for value in command] if isinstance(command, list) else []
    if not parts and isinstance(command, str):
        parts = [command]
    parsed = item.get("parsed_cmd")
    if isinstance(parsed, dict) and isinstance(parsed.get("cmd"), str):
        parts.append(parsed["cmd"])
    text = "\n".join(parts).casefold()
    if not any(name in text for name in ("goal_entry.py", "goal_await.py")):
        return False
    # Direct interpreter/script pairs avoid excluding commands that merely read
    # a Goal-await source file.
    for index, part in enumerate(parts):
        if re.search(r"(?:^|[\\/])goal_(?:entry|await)\.py\b", part.casefold()):
            before = parts[:index]
            if not before or any("python" in value.casefold() or
                                 re.search(r"(?:^|[\\/])py(?:\.exe)?$", value.strip('"\' ').casefold())
                                 for value in before):
                return True
    return bool(re.search(
        r"(?:python(?:\.exe)?|py(?:\.exe)?)\b[^\r\n;|]{0,300}"
        r"goal_(?:entry|await)\.py\b", text))


def _states(item: dict[str, Any]) -> dict[str, str]:
    raw = item.get("agents_states")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return {"<invalid>": "invalid"}
    result: dict[str, str] = {}
    for child, value in raw.items():
        if not isinstance(child, str) or not _UUID.fullmatch(child):
            return {"<invalid>": "invalid"}
        if isinstance(value, str):
            state = value.casefold()
        elif isinstance(value, dict) and any(key in value for key in
                                             ("completed", "errored", "failed", "interrupted")):
            state = next(key for key in ("completed", "errored", "failed", "interrupted")
                         if key in value)
        else:
            state = "invalid"
        result[child.lower()] = state
    return result


def _notification(record: dict[str, Any]) -> tuple[str, bool] | None:
    payload = _payload(record)
    if record.get("type") != "response_item" or payload.get("type") != "message":
        return None
    meta = payload.get("internal_chat_message_metadata_passthrough")
    if not isinstance(meta, dict):
        return None
    item_meta = meta.get("content_item_metadata")
    kinds = meta.get("content_item_kinds")
    values = kinds if isinstance(kinds, list) else []
    sources = item_meta if isinstance(item_meta, list) else []
    values += [kind for entry in sources if isinstance(entry, dict)
               for kind in entry.get("content_item_kinds", []) if isinstance(kind, str)]
    provenance = any(isinstance(entry, dict) and isinstance(entry.get("provenance"), dict)
                     and entry["provenance"].get("namespace") == "multi_agent_v1"
                     for entry in sources)
    if "multi_agent.subagent_notification" not in values and not provenance:
        return None
    text = "\n".join(value.get("text", "") for value in payload.get("content", [])
                      if isinstance(value, dict) and isinstance(value.get("text"), str))
    match = re.search(r"<subagent_notification>\s*(\{.*?\})\s*</subagent_notification>",
                      text, re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    child = value.get("agent_path") if isinstance(value, dict) else None
    status = value.get("status") if isinstance(value, dict) else None
    if not isinstance(child, str) or not _UUID.fullmatch(child):
        return None
    if isinstance(status, dict):
        terminal = any(str(key).casefold() in _TERMINAL for key in status)
    elif isinstance(status, str):
        terminal = status.casefold() in _TERMINAL
    else:
        terminal = False
    return (child.lower(), True) if terminal else None


def _hook_boundary(records: list[dict[str, Any]], start: int,
                   turn_id: str) -> int | None:
    boundary = None
    for index in range(start + 1, len(records)):
        payload = _payload(records[index])
        item = payload.get("item")
        fragments = item.get("fragments") if isinstance(item, dict) else None
        is_stop = isinstance(fragments, list) and any(
            isinstance(fragment, dict)
            and isinstance(fragment.get("hookRunId"), str)
            and fragment["hookRunId"].casefold().startswith("stop:")
            for fragment in fragments
        )
        if (payload.get("type") == "item_completed" and _turn(payload) == turn_id
                and isinstance(item, dict) and _kind(item) == "hookprompt"
                and is_stop):
            boundary = index
    return boundary


def _tool_result(item: dict[str, Any]) -> tuple[bool, str]:
    """Return (counts_as_activity, pending_child_or_empty)."""
    kind, status, name = _kind(item), _status(item), _name(item)
    if status in _PENDING:
        return False, "pending"
    if status not in _TERMINAL:
        return False, "invalid"
    if _control_name(name):
        return False, ""
    if kind == "filechange":
        return bool(item.get("changes")), ""
    if kind == "commandexecution":
        return not _goal_command(item), ""
    if kind == "collabagenttoolcall":
        states = _states(item)
        if "<invalid>" in states:
            return False, "invalid"
        return not (name == "wait" and not states), ""
    return kind in _TOOL_ITEMS, ""


def _live_wait(state: dict[str, Any]) -> bool | None:
    if state.get("state") != "WAITING":
        return False
    wait, ident = state.get("wait"), state.get("automation_id")
    if not isinstance(wait, dict) or not isinstance(ident, str) or not ident:
        return None
    required = ("waiting_for", "completion", "result_location")
    return all(isinstance(wait.get(key), str) and wait[key].strip() for key in required)


def assess(event: dict[str, Any], state: dict[str, Any],
           previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify tools completed since the previous cursor for this one turn."""
    turn_id = event.get("turn_id", "") if isinstance(event, dict) else ""
    turn_id = turn_id if isinstance(turn_id, str) else ""
    prior_cursor = previous.get("cursor", "") if isinstance(previous, dict) else ""
    cursor = prior_cursor if isinstance(prior_cursor, str) else ""
    try:
        if not isinstance(event, dict) or not isinstance(state, dict) or not turn_id:
            return _unknown("missing_turn", turn_id, cursor)
        thread_id = state.get("thread_id")
        workspace = state.get("workspace")
        if not isinstance(thread_id, str) or not isinstance(workspace, str):
            return _unknown("invalid_state", turn_id, cursor)
        thread_id = evidence._thread(thread_id)
        recovery = state.get("recovery")
        snapshot = recovery.get("snapshot") if isinstance(recovery, dict) else None
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        raw_path = event.get("transcript_path") or snapshot.get("transcript_path")
        path = Path(raw_path) if isinstance(raw_path, str) and raw_path else evidence._locate(thread_id, None)
        if not path.is_file():
            return _unknown("missing_transcript", turn_id, cursor)
        workspace_key = evidence._workspace_key(workspace)
        evidence._check_meta(evidence._metadata(path), thread_id, workspace_key)
        records = evidence._tail(path)
        current_cursor = _cursor(records[-1])
        starts = evidence._primary_starts(records)
        if not starts or starts[-1][1]["turn_id"] != turn_id:
            return _unknown("turn_mismatch", turn_id, cursor)
        start_index, _, trigger = starts[-1]
        automatic_turn = trigger in _AUTOMATIC_TRIGGERS
        context = evidence._context(records, turn_id, after=start_index,
                                    before=len(records))
        if not context:
            return _unknown("missing_context", turn_id, cursor)
        if context.get("cwd") is not None:
            if evidence._workspace_key(context["cwd"]) != workspace_key:
                return _unknown("workspace_mismatch", turn_id, cursor)
        if evidence._aborted(records, start_index, turn_id):
            return _result("interrupted", "interrupted", turn_id,
                           current_cursor, automatic_turn, 0)
        mode = context.get("collaboration_mode")
        if (isinstance(mode, dict) and isinstance(mode.get("mode"), str)
                and mode["mode"].casefold() == "plan"):
            return _result("plan", "plan_mode", turn_id,
                           current_cursor, automatic_turn, 0)
        if isinstance(previous, dict) and previous.get("turn_id") == turn_id:
            if not prior_cursor:
                # Older stop_hook records have no cursor.  Their prior Stop
                # feedback is the only conservative boundary available.
                boundary = _hook_boundary(records, start_index, turn_id)
                segment_start = (boundary + 1 if boundary is not None
                                 else len(records))
            elif not isinstance(prior_cursor, str) or not re.fullmatch(r"[0-9a-f]{64}", prior_cursor):
                return _unknown("missing_cursor", turn_id, cursor)
            else:
                positions = [index for index, record in enumerate(records)
                             if _cursor(record) == prior_cursor]
                if len(positions) != 1:
                    return _unknown("cursor_missing" if not positions else "cursor_ambiguous",
                                    turn_id, cursor)
                segment_start = positions[0] + 1
        elif isinstance(previous, dict) and previous.get("turn_id") not in (None, turn_id):
            segment_start = start_index + 1
        elif isinstance(previous, dict) and not previous.get("turn_id"):
            segment_start = start_index + 1
        elif previous is not None:
            return _unknown("invalid_previous", turn_id, cursor)
        else:
            segment_start = start_index + 1

        activities: set[str] = set()
        pending_items: dict[str, str] = {}
        pending_children: dict[str, str] = {}
        outer_calls: dict[str, str] = {}
        outer_outputs: set[str] = set()
        fallback_calls: dict[str, str] = {}
        fallback_outputs: set[str] = set()
        invalid = False

        for index in range(start_index + 1, len(records)):
            record, payload = records[index], _payload(records[index])
            record_turn = _turn(payload)
            if record_turn is not None and record_turn not in (turn_id, "auto-compact-1"):
                continue
            if payload.get("type") in ("item_started", "item_completed"):
                item = payload.get("item")
                if not isinstance(item, dict):
                    invalid = True
                    continue
                item_kind = _kind(item)
                if item_kind not in _TOOL_ITEMS:
                    continue
                if record_turn is None:
                    invalid = True
                    continue
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id:
                    invalid = True
                    continue
                counts, pending = _tool_result(item)
                if pending == "invalid":
                    invalid = True
                    continue
                if pending:
                    pending_items[item_id] = pending
                    continue
                pending_items.pop(item_id, None)
                if payload.get("type") == "item_completed" and counts and index >= segment_start:
                    activities.add(item_id)
                for child, value in _states(item).items():
                    if child == "<invalid>":
                        invalid = True
                    elif value in _PENDING:
                        pending_children[child] = value
                    else:
                        pending_children.pop(child, None)
            elif record.get("type") == "response_item":
                kind = payload.get("type")
                call_id = payload.get("call_id")
                if kind in ("custom_tool_call", "function_call") and isinstance(call_id, str):
                    name = payload.get("name") if isinstance(payload.get("name"), str) else ""
                    if _transport_name(name):
                        outer_calls[call_id] = name
                    elif not _control_name(name):
                        fallback_calls[call_id] = name
                elif kind in ("custom_tool_call_output", "function_call_output") and isinstance(call_id, str):
                    outer_outputs.add(call_id)
                    if call_id in fallback_calls and call_id not in fallback_outputs:
                        fallback_outputs.add(call_id)
                        if index >= segment_start:
                            activities.add("call:" + call_id)
                notice = _notification(records[index])
                if notice:
                    child, completed = notice
                    if completed and child in pending_children:
                        pending_children.pop(child)
                        if index >= segment_start:
                            activities.add("notice:" + child)

        if invalid:
            return _unknown("invalid_evidence", turn_id, cursor)
        live_wait = _live_wait(state)
        if live_wait is None:
            return _unknown("invalid_wait", turn_id, cursor)
        pending_outer = {call for call in outer_calls if call not in outer_outputs}
        pending_fallback = {call for call in fallback_calls
                            if call not in fallback_outputs and call not in outer_calls}
        if live_wait or pending_items or pending_children or pending_outer or pending_fallback:
            return _result("waiting", "pending", turn_id, current_cursor,
                           automatic_turn, len(activities))
        kind = "activity" if activities else "no_activity"
        return _result(kind, "ok" if activities else "no_tools", turn_id,
                       current_cursor, automatic_turn, len(activities))
    except (KeyError, OSError, TypeError, ValueError):
        return _unknown("invalid_evidence", turn_id, cursor)
