"""Agent runner: real Anthropic tool-use loop, or deterministic mock.

Every run is logged (prompt version, tool calls, source_ids cited, validation
retries) — this log is the eval + recalibration dataset (§11 guardrails).
"""
from __future__ import annotations

import contextvars
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from app import config
from app.tools import TOOL_DEFS, ToolDispatcher
from app.validators import AgentValidationError

T = TypeVar("T", bound=BaseModel)

# Which thread the current node belongs to. A contextvar rather than a parameter
# so observability does not have to be threaded through every call site (and so
# a missed call site degrades to "unattributed", never to a crash).
current_thread: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "plotline_current_thread", default=None)


def _clip(value: Any, limit: int = 6000) -> Any:
    """Cap a logged body. Debug surface, not an archive."""
    try:
        text = json.dumps(value, default=str)
    except Exception:
        return {"_unserialisable": str(type(value))}
    if len(text) <= limit:
        return json.loads(text)
    return {"_truncated": True, "_bytes": len(text), "preview": text[:limit]}


class AgentTruncated(ValueError):
    """The model hit the output cap mid-answer (stop_reason=max_tokens).

    Subclasses ValueError so the §3.9 retry loop picks it up — but it is NOT a
    validation failure and must never be parsed as one: extended-thinking
    tokens share the output budget, so a truncated response is a *complete
    prefix of valid JSON*, which json.loads reports as a confusing delimiter
    error. Naming it here keeps the honesty invariant: nothing silently
    accepted, and the retry tells the model the real reason.
    """


class AgentTransport(ValueError):
    """The connection dropped before a complete response arrived.

    Subclasses ValueError so the §3.9 retry loop picks it up — but it is NOT a
    validation failure and must not be reported to the model as one: there was
    no output to correct. A dropped stream is safe to re-issue verbatim.

    Before this existed, an httpx error escaped run_agent entirely: no log row
    was persisted (so the node vanished from observability), and a full paid
    rumination was discarded because one mid-stream blip hit the last node. On
    the production models that is minutes of successful, already-paid work
    thrown away for a network hiccup.
    """


class AgentHardFail(RuntimeError):
    """Validation still failing after max retries — surfaced to the user,
    never silently accepted or repaired (§3.9)."""

    def __init__(self, agent: str, errors: str):
        self.agent = agent
        self.errors = errors
        super().__init__(f"{agent}: output invalid after {config.MAX_VALIDATION_RETRIES} retries — {errors}")


@dataclass
class RunLog:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    agent: str = ""
    model: str = ""
    prompt_version: str = ""
    mock: bool = False
    attempts: int = 0
    validation_errors: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    cited_source_ids: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    # --- observability: which thread this node ran for, and the structured
    # payload in / object out. Metadata alone (versions, tool calls, timings)
    # tells you a node ran but not what it was asked or what it decided, which
    # is the first question every time. Bodies are capped — this is a debug
    # surface, not an archive.
    thread_id: Optional[str] = None
    node_input: Optional[dict[str, Any]] = None
    node_output: Optional[dict[str, Any]] = None
    started_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "agent": self.agent,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "mock": self.mock,
            "attempts": self.attempts,
            "validation_errors": self.validation_errors,
            "tool_calls": self.tool_calls,
            "cited_source_ids": self.cited_source_ids,
            "duration_s": round(self.duration_s, 2),
            "thread_id": self.thread_id,
            "node_input": self.node_input,
            "node_output": self.node_output,
            "started_at": self.started_at,
        }


_PROMPT_CACHE: dict[str, tuple[str, str]] = {}


def load_prompt(name: str) -> tuple[str, str]:
    """Returns (text, version). Prompts are versioned files in /prompts —
    never hardcoded in code (§11)."""
    if name in _PROMPT_CACHE:
        return _PROMPT_CACHE[name]
    path = config.PROMPTS_DIR / f"{name}.md"
    text = path.read_text()
    match = re.search(r"version:\s*([\w.\-]+)", text)
    version = match.group(1) if match else "unversioned"
    _PROMPT_CACHE[name] = (text, version)
    return text, version


def build_system(
    prompt_name: str,
    replacements: Optional[dict[str, str]] = None,
    preludes: Optional[list[str]] = None,
) -> tuple[str, str]:
    """shared_policy + any preludes + the agent's own prompt.

    A prelude is a prompt several agents share (the council doctrine is the
    first). It sits AFTER shared_policy so it can narrow it, and BEFORE the
    agent body so the body can narrow the prelude in turn — specific beats
    general as you read down.

    The returned version is composite when preludes are present
    (`seat_brand@2.0.0+doctrine@3.0.0`), because "which reviewer said this" is
    an audit question and the seat file's own version cannot answer it. With no
    preludes the version is unchanged, so every existing caller logs what it
    always logged.
    """
    policy, _ = load_prompt("shared_policy")
    parts = [policy]
    prelude_versions: list[str] = []
    for name in preludes or []:
        text, prelude_version = load_prompt(name)
        parts.append(text)
        prelude_versions.append(f"{name.rsplit('/', 1)[-1]}@{prelude_version}")

    body, version = load_prompt(prompt_name)
    for key, value in (replacements or {}).items():
        body = body.replace("{" + key + "}", value)
    parts.append(body)

    if not prelude_versions:
        return "\n\n".join(parts), version
    stem = prompt_name.rsplit("/", 1)[-1]
    return "\n\n".join(parts), "+".join([f"{stem}@{version}"] + prelude_versions)


def extract_json(text: str) -> Any:
    """Agents must output strict JSON; tolerate a fenced block but nothing else."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in agent output")
    return json.loads(stripped[start : end + 1])


def unwrap_envelope(data: Any, schema: Type[BaseModel]) -> Any:
    """Real-mode models following the Addendum-01 conversational protocol
    sometimes wrap their payload in an AgentMessage envelope ({text, artifacts,
    question}). The SERVER owns the envelope — if the expected schema isn't
    AgentMessage itself, pull the payload back out of the wrapper."""
    if (
        schema.__name__ != "AgentMessage"
        and isinstance(data, dict)
        and isinstance(data.get("artifacts"), list)
        and ("text" in data or "question" in data)
    ):
        for artifact in data["artifacts"]:
            payload = artifact.get("payload") if isinstance(artifact, dict) else None
            if isinstance(payload, dict):
                try:
                    schema.model_validate(payload)
                    return payload
                except ValidationError:
                    continue
        # single artifact but its payload didn't validate → return it anyway so
        # the retry error names the REAL schema gap, not the envelope wrapper
        if len(data["artifacts"]) == 1 and isinstance(data["artifacts"][0], dict):
            payload = data["artifacts"][0].get("payload")
            if isinstance(payload, dict):
                return payload
    return data


def _llm_call(
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    dispatcher: Optional[ToolDispatcher],
    use_tools: bool,
) -> str:
    import anthropic
    import httpx

    client = anthropic.Anthropic()
    kwargs: dict[str, Any] = dict(model=model, max_tokens=config.MAX_OUTPUT_TOKENS, system=system)
    if "sonnet-4-6" in model:
        kwargs["thinking"] = {"type": "adaptive"}
    if use_tools:
        kwargs["tools"] = TOOL_DEFS

    convo = list(messages)
    for _ in range(16):  # tool-loop cap
        # Stream, always. A budget big enough for a thinking chair + a large
        # Feedback object implies a generation the SDK refuses to run
        # non-streamed ("Streaming is required for operations that may take
        # longer than 10 minutes"). The final message has the same shape, so
        # everything below is unchanged.
        try:
            with client.messages.stream(messages=convo, **kwargs) as stream:
                resp = stream.get_final_message()
        except (httpx.TransportError, anthropic.APIConnectionError) as exc:
            # Connection-class only. An APIStatusError (401/403/400) is a real
            # answer from the server and must surface, not spin.
            raise AgentTransport(
                f"the connection dropped before a complete response arrived ({type(exc).__name__}: "
                f"{exc}) — nothing was received, so nothing is wrong with the request"
            ) from exc
        if resp.stop_reason == "tool_use":
            convo.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    output = (
                        dispatcher.dispatch(block.name, dict(block.input))
                        if dispatcher
                        else json.dumps({"error": "no tools available"})
                    )
                    results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": output}
                    )
            convo.append({"role": "user", "content": results})
            continue
        if resp.stop_reason == "pause_turn":
            convo.append({"role": "assistant", "content": resp.content})
            continue
        if resp.stop_reason == "max_tokens":
            # Never hand a truncated body to the JSON parser — it is a valid
            # prefix, so the parser blames syntax and hides the real cause.
            usage = getattr(resp, "usage", None)
            thinking = getattr(getattr(usage, "output_tokens_details", None), "thinking_tokens", None)
            raise AgentTruncated(
                f"output hit the {config.MAX_OUTPUT_TOKENS}-token cap before the answer was complete "
                f"(generated {getattr(usage, 'output_tokens', '?')} tokens"
                + (f", {thinking} of them thinking" if thinking else "")
                + ") — the response is incomplete, not invalid"
            )
        # join every text block: one long answer can arrive as several.
        return "".join(b.text for b in resp.content if b.type == "text")
    raise RuntimeError("tool loop exceeded 16 iterations")


def run_agent(
    *,
    agent: str,
    prompt_name: str,
    model: str,
    user_payload: dict[str, Any],
    schema: Type[T],
    dispatcher: Optional[ToolDispatcher] = None,
    validate: Optional[Callable[[T], T]] = None,
    prompt_replacements: Optional[dict[str, str]] = None,
    preludes: Optional[list[str]] = None,
    mock_fn: Optional[Callable[[dict[str, Any], Optional[ToolDispatcher]], dict[str, Any]]] = None,
    use_tools: bool = True,
    extra_content_blocks: Optional[list[dict[str, Any]]] = None,
) -> tuple[T, RunLog]:
    """Validation retry loop (§3.9): invalid output → re-run with the error
    (max 2 retries) → AgentHardFail. Applies to mock output too — the mock
    goes through the same schema + server-side validation path."""
    system, version = build_system(prompt_name, prompt_replacements, preludes)
    log = RunLog(agent=agent, model=model, prompt_version=version, mock=config.MOCK_LLM,
                 thread_id=current_thread.get(), started_at=time.time())
    log.node_input = _clip(user_payload)
    started = time.time()

    last_error: Optional[str] = None
    last_truncated = False
    last_transport = False
    for attempt in range(config.MAX_VALIDATION_RETRIES + 1):
        log.attempts = attempt + 1
        raw_text: Optional[str] = None
        try:
            if config.MOCK_LLM:
                if mock_fn is None:
                    raise RuntimeError(f"no mock for agent {agent}")
                data = mock_fn(user_payload, dispatcher)
            else:
                content: list[dict[str, Any]] = [
                    {"type": "text", "text": json.dumps(user_payload, default=str)}
                ]
                if extra_content_blocks:
                    content = extra_content_blocks + content
                messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
                # A transport failure produced no output, so there is nothing to
                # correct. Re-issue the request verbatim; telling the model its
                # answer "failed validation" would make it rewrite a good answer
                # it never got to send.
                if last_error and not last_transport:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                (
                                    "Your previous output was CUT OFF before it finished — it exceeded "
                                    "the output budget. The structure was not wrong; it was too long. "
                                    "Return the SAME JSON shape, complete, but materially shorter: keep "
                                    "every required key, cut prose in reason/claim/note fields to one "
                                    "tight sentence each.\nREASON:\n"
                                    if last_truncated
                                    else "Your previous output failed validation. Fix ONLY what is invalid "
                                    "and return the full corrected JSON.\nVALIDATION ERROR:\n"
                                )
                                + last_error
                            ),
                        }
                    )
                text = _llm_call(model, system, messages, dispatcher, use_tools)
                raw_text = text
                data = unwrap_envelope(extract_json(text), schema)

            obj = schema.model_validate(data)
            if validate is not None:
                obj = validate(obj)
            log.duration_s = time.time() - started
            if dispatcher:
                log.tool_calls = dispatcher.calls
            log.cited_source_ids = sorted(_cited_ids(obj))
            log.node_output = _clip(obj.model_dump(mode="json"))
            _persist_log(log)
            return obj, log
        except (ValidationError, AgentValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            last_truncated = isinstance(exc, AgentTruncated)
            last_transport = isinstance(exc, AgentTransport)
            log.validation_errors.append(last_error[:2000])
            if last_transport:
                # A blip needs a moment to clear; an instant re-issue tends to
                # meet the same one. Bounded by MAX_VALIDATION_RETRIES either way.
                time.sleep(2 * (attempt + 1))
            # Keep the body that failed. Without it a truncation and a genuine
            # schema bug read identically in the log, which is what made this
            # class of failure cost a full re-run to diagnose.
            _persist_raw(log, attempt, raw_text)

    log.duration_s = time.time() - started
    _persist_log(log)
    raise AgentHardFail(agent, last_error or "unknown validation failure")


def _cited_ids(obj: BaseModel) -> set[str]:
    ids: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            sid = value.get("source_id")
            if isinstance(sid, str):
                ids.add(sid)
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(obj.model_dump(mode="json"))
    ids.discard("model")
    return ids


def _persist_log(log: RunLog) -> None:
    path: Path = config.LOG_DIR / "agent_runs.jsonl"
    with path.open("a") as fh:
        fh.write(json.dumps(log.to_dict(), default=str) + "\n")


def _persist_raw(log: RunLog, attempt: int, raw: Optional[str]) -> None:
    """Dump the exact body that failed to parse/validate, next to the run log."""
    if not raw:
        return
    raw_dir: Path = config.LOG_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{log.run_id}-{log.agent}-attempt{attempt + 1}.txt").write_text(raw)
