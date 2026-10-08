import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from engine import config, grading

from . import service

TEXT, BOOL, INT, HANDLE = "text", "bool", "int", "handle"
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "synchrono-service/flow")
_KEEP = object()


@dataclass
class Node:
    component: str
    display: str
    inputs: dict
    run: Callable
    node_id: str = ""


@dataclass
class Flow:
    endpoint: str
    name: str
    nodes: list[Node]
    forced: dict = field(default_factory=dict)
    flow_id: str = ""
    chat_id: str = ""


def node_id(endpoint: str, component: str) -> str:
    return f"{component}-{hashlib.md5(f'{endpoint}:{component}'.encode()).hexdigest()[:5]}"


def _payload_json(text: str, required: bool) -> dict:
    text = (text or "").strip()
    if not text and not required:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"payload bukan JSON yang sah: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError("payload harus objek JSON, bukan array atau skalar")
    return payload


def _grading_dispatch(v: dict, _) -> str:
    payload = _payload_json(v["payload"], False)
    defaults = {k: v[k] for k in ("file_id", "s3_bucket", "parquet_key", "s3_endpoint",
                                  "callback_url", "callback_token")}
    return json.dumps(service.dispatch_grading(payload, defaults), ensure_ascii=False)


def _grading_status(v: dict, _) -> str:
    file_id = (v["file_id"] or "").strip() or None
    job_id = (v["job_id"] or "").strip() or None
    if not file_id and not job_id:
        raise ValueError("Isi salah satu: file_id atau job_id")
    return json.dumps(service.grading_status(file_id, job_id), ensure_ascii=False)


def _rules_get(v: dict, _) -> str:
    raw = (v["grade_id"] or "").strip()
    grade = None
    if raw:
        try:
            grade = int(raw)
        except ValueError as e:
            raise ValueError(f"grade_id harus angka 1-6, dapat: {raw!r}") from e
        if grade not in range(1, 7):
            raise ValueError(f"grade_id harus 1-6, dapat: {grade}")
    result = config.read_all(grade)
    if grade is not None and not result["grades"]:
        raise ValueError(f"Grade {grade} tidak ada di konfigurasi.")
    return json.dumps(result, ensure_ascii=False, default=str)


def _rules_update(v: dict, _) -> str:
    text = (v["payload"] or "").strip()
    if not text:
        raise ValueError("payload kosong")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"payload bukan JSON yang sah: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError("payload harus objek JSON")
    dry = bool(payload.get("dryRun", False)) or bool(v["dry_run"])
    by = payload.get("updatedBy") or payload.get("updated_by")
    grade = payload.get("gradeId", payload.get("grade_id"))
    if "global" in payload:
        if grade is not None:
            raise ValueError("Pilih salah satu: `gradeId` (aturan satu grade) "
                             "atau `global` (nilai global), bukan keduanya.")
        items = payload["global"]
        if not isinstance(items, dict):
            raise ValueError("`global` harus objek {grading?, matching?}.")
        label = "global"
        result = config.update_global({k: items.get(k) for k in ("grading", "matching")},
                                      by=by, dry_run=dry)
    else:
        if grade is None:
            raise ValueError("Field `gradeId` wajib diisi (atau `global` untuk nilai global).")
        try:
            grade = int(grade)
        except (TypeError, ValueError) as e:
            raise ValueError(f"gradeId harus angka, dapat: {grade!r}") from e
        label = f"grade {grade}"
        result = config.update(grade, {k: payload.get(k) for k in ("criteria", "score",
                                                                   "matching")},
                               by=by, dry_run=dry)
    service.log_config(result, label, dry)
    return json.dumps(result, ensure_ascii=False, default=str)


def _matching_dispatch(v: dict, _) -> str:
    payload = _payload_json(v["payload"], True)
    return json.dumps(service.dispatch_matching(payload), ensure_ascii=False)


def _g1(v: dict, _) -> dict:
    return grading.open_session({k: v[k] for k in ("file_id", "s3_bucket", "parquet_key",
                                                   "s3_endpoint", "enriched_key")})


def _g5(_, s: dict) -> dict:
    return grading.load_enriched(grading.load_kl(grading.write_enriched(s)))


def _g6(_, s: dict) -> str:
    return json.dumps(grading.build_result(s), ensure_ascii=False, indent=2)


SESSION = {"session": {"type": HANDLE}}
FLOWS = {f.endpoint: f for f in [
    Flow("grading-dispatch", "Synchrono Grading Dispatch", [
        Node("GradingDispatch", "API 1. Dispatch Grading Job", {
            "payload": {"type": TEXT}, "file_id": {"type": TEXT},
            "s3_bucket": {"type": TEXT, "value": "syncrono-uploads"},
            "parquet_key": {"type": TEXT}, "s3_endpoint": {"type": TEXT},
            "callback_url": {"type": TEXT}, "callback_token": {"type": TEXT}},
            _grading_dispatch)]),
    Flow("grading-status", "Synchrono Grading Status", [
        Node("GradingStatus", "API 2. Grading Status",
             {"file_id": {"type": TEXT}, "job_id": {"type": TEXT}}, _grading_status)]),
    Flow("grading", "Synchrono Grading Pipeline", [
        Node("OpenGradingSession", "G1. Open Grading Session", {
            "file_id": {"type": TEXT}, "s3_bucket": {"type": TEXT, "value": "syncrono-uploads"},
            "parquet_key": {"type": TEXT}, "s3_endpoint": {"type": TEXT},
            "enriched_key": {"type": TEXT}}, _g1),
        Node("LoadRawParquet", "G2. Load Raw Parquet", SESSION,
             lambda _, s: grading.load_raw(s)),
        Node("FlagAnomalies", "G3. Clean NIK & Flag Anomalies", SESSION,
             lambda _, s: grading.clean_and_flag(s)),
        Node("ScoreAndGrade", "G4. Score & Grade", SESSION,
             lambda _, s: grading.score_and_grade(s)),
        Node("WriteEnrichedParquet", "G5. Write Enriched Parquet", SESSION, _g5),
        Node("BuildCallbackPayload", "G6. Build Callback Payload", SESSION, _g6),
    ]),
    Flow("config-rules", "Synchrono Config Rules", [
        Node("GradingRuleGet", "API 3. Get Grading Rules", {"grade_id": {"type": TEXT}},
             _rules_get)]),
    Flow("config-rules-update", "Synchrono Config Rules Update", [
        Node("GradingRuleUpdate", "API 4. Update Grading Rule",
             {"payload": {"type": TEXT}, "dry_run": {"type": BOOL, "value": False}},
             _rules_update)]),
    Flow("matching-dispatch", "Synchrono Matching Dispatch", [
        Node("MatchingDispatch", "API. Dispatch Matching Job", {"payload": {"type": TEXT}},
             _matching_dispatch)], forced={"MatchingDispatch": "MatchingDispatch-b4819"}),
]}

for _f in FLOWS.values():
    _f.flow_id = str(uuid.uuid5(_NAMESPACE, _f.endpoint))
    _f.chat_id = node_id(_f.endpoint, "ChatOutput")
    for _n in _f.nodes:
        _n.node_id = _f.forced.get(_n.component) or node_id(_f.endpoint, _n.component)
_BY_ID = {f.flow_id: f for f in FLOWS.values()}


def find(identifier: str) -> Flow | None:
    return FLOWS.get(identifier) or _BY_ID.get(identifier)


class ComponentError(Exception):
    def __init__(self, display: str, error: Exception):
        super().__init__(str(error))
        self.display = display
        self.error = error

    def detail(self) -> str:
        return json.dumps({
            "message": f"Error running graph: Error building Component {self.display}: "
                       f"\n\n{self.error}",
            "traceback": None, "description": None, "code": None, "suggestion": None,
        }, ensure_ascii=False, separators=(",", ":"))


def _initial(spec: dict):
    if "value" in spec:
        return spec["value"]
    return {TEXT: "", BOOL: False, INT: 0}.get(spec["type"])


def _adapt(spec: dict, value):
    if isinstance(value, dict):
        if "value" not in value:
            return _KEEP
        value = value["value"]
    kind = spec["type"]
    if kind == TEXT:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            items = [str(x) for x in value]
            raise ValueError(
                "1 validation error for MessageTextInput\nvalue\n"
                f"  Value error, Invalid value type <class 'list'> "
                f"[type=value_error, input_value={items!r}, input_type=list]\n"
                "    For further information visit "
                "https://errors.pydantic.dev/2.13/v/value_error")
        return _KEEP
    if kind == BOOL:
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        return bool(value)
    if kind == INT:
        return int(value)
    return value


def run(flow: Flow, tweaks: dict | None) -> str:
    tweaks = tweaks or {}
    output, sessions = None, []
    try:
        for node in flow.nodes:
            try:
                values = {name: _initial(spec) for name, spec in node.inputs.items()}
                for name, value in (tweaks.get(node.node_id) or {}).items():
                    spec = node.inputs.get(name)
                    if spec is not None and spec["type"] != HANDLE:
                        adapted = _adapt(spec, value)
                        if adapted is not _KEEP:
                            values[name] = adapted
                output = node.run(values, output)
                if isinstance(output, dict) and output.get("con") is not None:
                    sessions.append(output["con"])
            except Exception as e:  # noqa: BLE001
                raise ComponentError(node.display, e) from e
    finally:
        for con in sessions:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
    return output if isinstance(output, str) else str(output)


def envelope(flow: Flow, text: str, input_value: str | None, session_id: str | None) -> dict:
    source = flow.nodes[-1]
    sid = session_id or flow.flow_id
    run_id = str(uuid.uuid4())
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f UTC")
    properties = {
        "text_color": None, "background_color": None, "edited": False,
        "source": {"id": source.node_id, "display_name": source.display,
                   "source": source.display},
        "icon": None, "allow_markdown": False, "positive_feedback": None,
        "state": "complete", "targets": [], "usage": None, "build_duration": None,
    }
    data = {
        "timestamp": stamp, "sender": "Machine", "sender_name": "AI", "session_id": sid,
        "context_id": "", "text": text, "files": [], "error": False, "edit": False,
        "properties": properties, "category": "message", "content_blocks": [],
        "session_metadata": {"graph_run_id": run_id}, "id": str(uuid.uuid4()),
        "flow_id": flow.flow_id, "run_id": run_id, "duration": None,
    }
    message = {
        "text_key": "text", "data": data, "default_value": "", "sender": "Machine",
        "sender_name": "AI", "files": [], "session_id": sid, "context_id": "",
        "run_id": run_id, "timestamp": stamp, "flow_id": flow.flow_id, "error": False,
        "edit": False, "properties": properties, "category": "message", "content_blocks": [],
        "duration": None, "session_metadata": {"graph_run_id": run_id}, "text": text,
    }
    return {
        "session_id": sid,
        "outputs": [{
            "inputs": {"input_value": input_value or ""},
            "outputs": [{
                "results": {"message": message},
                "artifacts": {"message": text, "sender": "Machine", "sender_name": "AI",
                              "files": [], "type": "object"},
                "outputs": {"message": {"message": text, "type": "text"}},
                "logs": {"message": []},
                "messages": [{"message": text, "sender": "Machine", "sender_name": "AI",
                              "session_id": sid, "stream_url": None,
                              "component_id": flow.chat_id, "files": [], "type": "text"}],
                "timedelta": None, "duration": None, "component_display_name": "Chat Output",
                "component_id": flow.chat_id, "used_frozen_result": False, "token_usage": None,
            }],
        }],
    }


def timed_run(flow: Flow, tweaks: dict) -> str:
    started = time.perf_counter()
    try:
        text = run(flow, tweaks)
    except ComponentError as e:
        print(f"[run] {flow.endpoint} FAILED ({(time.perf_counter() - started) * 1000:.0f} ms) "
              f"in {e.display}: {type(e.error).__name__}: {e.error}", flush=True)
        raise
    print(f"[run] {flow.endpoint} ok ({(time.perf_counter() - started) * 1000:.0f} ms)",
          flush=True)
    return text
