"""Agent runner: real Anthropic tool-use loop, or deterministic mock.

Every run is logged (prompt version, tool calls, source_ids cited, validation
retries) — this log is the eval + recalibration dataset (§11 guardrails).
"""
from __future__ import annotations

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


def build_system(prompt_name: str, replacements: Optional[dict[str, str]] = None) -> tuple[str, str]:
    policy, _ = load_prompt("shared_policy")
    body, version = load_prompt(prompt_name)
    for key, value in (replacements or {}).items():
        body = body.replace("{" + key + "}", value)
    return policy + "\n\n" + body, version


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

    client = anthropic.Anthropic()
    kwargs: dict[str, Any] = dict(model=model, max_tokens=16000, system=system)
    if "sonnet-4-6" in model:
        kwargs["thinking"] = {"type": "adaptive"}
    if use_tools:
        kwargs["tools"] = TOOL_DEFS

    convo = list(messages)
    for _ in range(16):  # tool-loop cap
        resp = client.messages.create(messages=convo, **kwargs)
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
        return next((b.text for b in resp.content if b.type == "text"), "")
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
    mock_fn: Optional[Callable[[dict[str, Any], Optional[ToolDispatcher]], dict[str, Any]]] = None,
    use_tools: bool = True,
    extra_content_blocks: Optional[list[dict[str, Any]]] = None,
) -> tuple[T, RunLog]:
    """Validation retry loop (§3.9): invalid output → re-run with the error
    (max 2 retries) → AgentHardFail. Applies to mock output too — the mock
    goes through the same schema + server-side validation path."""
    system, version = build_system(prompt_name, prompt_replacements)
    log = RunLog(agent=agent, model=model, prompt_version=version, mock=config.MOCK_LLM)
    started = time.time()

    last_error: Optional[str] = None
    for attempt in range(config.MAX_VALIDATION_RETRIES + 1):
        log.attempts = attempt + 1
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
                if last_error:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Your previous output failed validation. Fix ONLY what is invalid "
                                "and return the full corrected JSON.\nVALIDATION ERROR:\n" + last_error
                            ),
                        }
                    )
                text = _llm_call(model, system, messages, dispatcher, use_tools)
                data = unwrap_envelope(extract_json(text), schema)

            obj = schema.model_validate(data)
            if validate is not None:
                obj = validate(obj)
            log.duration_s = time.time() - started
            if dispatcher:
                log.tool_calls = dispatcher.calls
            log.cited_source_ids = sorted(_cited_ids(obj))
            _persist_log(log)
            return obj, log
        except (ValidationError, AgentValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            log.validation_errors.append(last_error[:2000])

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
