"""
Runtime metrics utilities for Cascade.

The module provides a small, dependency-free event recorder. It is intentionally
file-based so a run can be inspected after a crash and so metrics do not depend
on an external observability service. Events are written as JSON Lines and kept
in memory only for the compact summary produced at the end of a run.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections import defaultdict
from contextlib import ContextDecorator
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from types import TracebackType
from typing import Any


JsonDict = dict[str, Any]
LLMBucket = dict[str, Any]


_current_recorder: ContextVar["MetricsRecorder | None"] = ContextVar("cascade_metrics_recorder", default=None)
_current_context: ContextVar[JsonDict] = ContextVar("cascade_metrics_context", default={})


def utc_now_iso() -> str:
    """
    Return the current UTC timestamp in ISO-8601 form.

    The returned string uses a trailing ``Z`` instead of ``+00:00`` so event
    records are compact and easy to recognize as UTC timestamps.

    :return: UTC timestamp string, for example ``2026-06-16T12:34:56.789Z``.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def make_run_id() -> str:
    """
    Create a human-sortable run identifier.

    The ID contains a UTC timestamp and a short UUID suffix, which keeps related
    output files easy to correlate without requiring a central run registry.

    :return: Run identifier string in ``YYYYMMDDTHHMMSSZ-xxxxxxxx`` form.
    """
    timestamp: str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _jsonable(value: Any) -> Any:
    """
    Convert a value into something that can be serialized by ``json.dumps``.

    OpenAI SDK responses and nested objects may expose ``model_dump`` rather
    than being plain dictionaries. This helper preserves JSON-compatible values,
    recursively converts common containers, and falls back to ``str(value)`` for
    objects with no obvious JSON representation.

    :param value: Any value that may need to be persisted in a metrics event.
    :return: A JSON-serializable equivalent of ``value``.
    """
    try:
        json.dumps(value)
        return value
    except TypeError:
        if hasattr(value, "model_dump"):
            return _jsonable(value.model_dump())
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(v) for v in value]
        return str(value)


def _read_attr_or_key(value: Any, key: str) -> Any | None:
    """
    Read ``key`` from either a dictionary or an object attribute.

    The OpenAI SDK and test doubles can represent response metadata both ways,
    so callers use this helper to avoid duplicating shape checks.

    :param value: Mapping, object, or ``None`` to read from.
    :param key: Dictionary key or attribute name to look up.
    :return: The found value, or ``None`` when unavailable.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def normalize_usage(usage: Any) -> JsonDict:
    """
    Normalize LLM usage metadata to Cascade's common token field names.

    Supports Chat Completions-style usage fields
    (``prompt_tokens`` / ``completion_tokens``) and Responses-style fields
    (``input_tokens`` / ``output_tokens``). Missing values become ``0`` and the
    original usage object is included as ``raw_usage`` when available.

    :param usage: Usage metadata as a dict/object, or ``None``.
    :return: Dict with ``input_tokens``, ``output_tokens``, ``total_tokens``,
        and ``raw_usage``.
    """
    raw_usage: Any | None = _jsonable(usage) if usage is not None else None

    input_tokens: Any | None = _read_attr_or_key(usage, "input_tokens")
    if input_tokens is None:
        input_tokens = _read_attr_or_key(usage, "prompt_tokens")

    output_tokens: Any | None = _read_attr_or_key(usage, "output_tokens")
    if output_tokens is None:
        output_tokens = _read_attr_or_key(usage, "completion_tokens")

    total_tokens: Any | None = _read_attr_or_key(usage, "total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "total_tokens": total_tokens or 0,
        "raw_usage": raw_usage,
    }


def extract_response_usage(response: Any) -> JsonDict:
    """
    Extract and normalize token usage from an API response object.

    Returns the same dictionary as :func:`normalize_usage`. If the response has
    no ``usage`` field, all token counts are zero.

    :param response: OpenAI-compatible API response object or dict.
    :return: Normalized usage dictionary.
    """
    return normalize_usage(_read_attr_or_key(response, "usage"))


def extract_finish_reason(response: Any) -> Any | None:
    """
    Return the first choice's finish reason when present.

    The value is useful for diagnosing truncated generations or tool-call
    continuations. If the response has no choices, returns ``None``.

    :param response: OpenAI-compatible API response object or dict.
    :return: Finish reason string, or ``None``.
    """
    choices: list[Any] = _read_attr_or_key(response, "choices") or []
    if not choices:
        return None
    first_choice: Any = choices[0]
    return _read_attr_or_key(first_choice, "finish_reason")


def set_current_recorder(recorder: "MetricsRecorder") -> Token["MetricsRecorder | None"]:
    """
    Install ``recorder`` as the current metrics sink for this context.

    Returns a contextvars token that must be passed to
    :func:`reset_current_recorder` when the run is finished.

    :param recorder: :class:`MetricsRecorder` instance to make active.
    :return: Context variable token for later reset.
    """
    return _current_recorder.set(recorder)


def reset_current_recorder(token: Token["MetricsRecorder | None"]) -> None:
    """
    Restore the previous current recorder using a token from
    :func:`set_current_recorder`.

    :param token: Context variable token returned by
        :func:`set_current_recorder`.
    :return: ``None``.
    """
    _current_recorder.reset(token)


def get_current_recorder() -> "MetricsRecorder | None":
    """
    Return the active :class:`MetricsRecorder`, or ``None`` when metrics are off.

    :return: Active recorder instance or ``None``.
    """
    return _current_recorder.get()


class MetricContext(ContextDecorator):
    """
    ContextDecorator that applies metrics attribution fields.

    Instances are reusable, so they can safely be used both in ``with`` blocks
    and as decorators around functions that may be called multiple times.
    """

    def __init__(self, fields: JsonDict) -> None:
        """
        Store the attribution fields to apply on entry.

        :param fields: Context fields to merge into active metrics context.
        :return: ``None``.
        """
        self.fields: JsonDict = fields
        self.tokens: list[Token[JsonDict]] = []

    def __enter__(self) -> "MetricContext":
        """
        Apply this context's fields and return the context manager instance.

        :return: This ``MetricContext`` instance.
        """
        current: JsonDict = dict(_current_context.get())
        current.update({k: v for k, v in self.fields.items() if v is not None})
        self.tokens.append(_current_context.set(current))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """
        Restore the previous metrics context.

        :param exc_type: Exception type if the wrapped block raised.
        :param exc: Exception instance if the wrapped block raised.
        :param traceback: Traceback if the wrapped block raised.
        :return: ``False`` so exceptions are never suppressed.
        """
        _current_context.reset(self.tokens.pop())
        return False


def metric_context(**fields: Any) -> MetricContext:
    """
    Temporarily add attribution fields to all metrics events in this context.

    Nested contexts inherit existing fields and override only keys they provide.
    ``None`` values are ignored so callers can pass optional metadata without
    removing previously set context.

    :param fields: Attribution fields to merge into the current context.
    :return: :class:`MetricContext` usable in a ``with`` block or as a function
        decorator.
    """
    return MetricContext(dict(fields))


def get_metric_context() -> JsonDict:
    """
    Return a shallow copy of the currently active metric context.

    :return: Dict containing the active attribution fields.
    """
    return dict(_current_context.get())


def record_event(event_type: str, **fields: Any) -> None:
    """
    Record an event on the active recorder if metrics are enabled.

    This is a no-op when no current recorder has been installed. It lets low
    level code emit metrics without knowing whether it is running inside a full
    Cascade pipeline invocation.

    :param event_type: Event name used for grouping in metrics summaries.
    :param fields: Event-specific metadata fields.
    :return: ``None``.
    """
    recorder: MetricsRecorder | None = get_current_recorder()
    if recorder is None:
        return
    recorder.record_event(event_type, **fields)


def record_metric_event(event_type: str, **fields: Any) -> None:
    """
    Compatibility wrapper for the public metrics event helper.

    Several Cascade modules call this more explicit name. Keep it as the stable
    helper and delegate to :func:`record_event`.

    :param event_type: Event name used for grouping in metrics summaries.
    :param fields: Event-specific metadata fields.
    :return: ``None``.
    """
    record_event(event_type, **fields)


class TimeBlock(ContextDecorator):
    """
    ContextDecorator that records elapsed time for a block or function call.

    Instances are reusable, so they can safely be used as decorators around
    functions that may be called multiple times.
    """

    def __init__(self, event_type: str, fields: JsonDict) -> None:
        """
        Store the event type and metadata fields for timing events.

        :param event_type: Event name to record when the context exits.
        :param fields: Metadata fields to include on the timing event.
        :return: ``None``.
        """
        self.event_type: str = event_type
        self.fields: JsonDict = fields
        self.starts: list[float] = []

    def __enter__(self) -> "TimeBlock":
        """
        Start timing and return the context manager instance.

        :return: This ``TimeBlock`` instance.
        """
        self.starts.append(time.perf_counter())
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """
        Record elapsed time and propagate any exception.

        :param exc_type: Exception type if the wrapped block raised.
        :param exc: Exception instance if the wrapped block raised.
        :param traceback: Traceback if the wrapped block raised.
        :return: ``False`` so exceptions are never suppressed.
        """
        elapsed: float = time.perf_counter() - self.starts.pop()
        if exc_type is not None:
            error_message: str = str(exc) if exc is not None else ""
            error_type: str = exc_type.__name__
            record_event(
                self.event_type,
                elapsed_seconds=elapsed,
                success=False,
                error_type=error_type,
                error_message=error_message,
                **self.fields,
            )
            return False

        record_event(
            self.event_type,
            elapsed_seconds=elapsed,
            success=True,
            **self.fields,
        )
        return False


def time_block(event_type: str, **fields: Any) -> TimeBlock:
    """
    Measure a block and record a timing event when it exits.

    On normal exit, records ``success=True``. If the block raises, records
    ``success=False`` with the exception type/message and then re-raises the
    original exception.

    :param event_type: Event name to record for the timed block.
    :param fields: Metadata fields to include on the timing event.
    :return: :class:`TimeBlock` usable in a ``with`` block or as a function
        decorator.
    """
    return TimeBlock(event_type, dict(fields))


class MetricsRecorder:
    """
    Append-only event recorder and in-memory summary aggregator.

    A recorder owns one Cascade run. It writes every event to
    ``metrics.events.jsonl`` immediately and accumulates enough in-memory state
    to write ``metrics.summary.json`` at the end of the invocation. Write
    failures are intentionally non-fatal; metrics must not break analysis.
    """

    def __init__(self, output_path: str, run_id: str | None = None, config_snapshot: Any | None = None) -> None:
        """
        Initialize a recorder for ``output_path`` and emit the first event.

        :param output_path: Directory where metrics files are written.
        :param run_id: Optional explicit run identifier. A timestamp-based ID is
            generated when omitted.
        :param config_snapshot: Optional pipeline config to include in the run
            start event and final summary.
        :return: ``None``.
        """
        self.output_path: str = output_path
        self.run_id: str = run_id or make_run_id()
        self.config_snapshot: Any = _jsonable(config_snapshot or {})
        self.started_at: str = utc_now_iso()
        self.started_perf: float = time.perf_counter()
        self.events_path: str = os.path.join(output_path, "metrics.events.jsonl")
        self.summary_path: str = os.path.join(output_path, "metrics.summary.json")
        self.event_count: int = 0
        self.write_failures: int = 0

        self.events_by_type: defaultdict[str, int] = defaultdict(int)
        self.failures_by_type: defaultdict[str, int] = defaultdict(int)
        self.elapsed_by_type: defaultdict[str, float] = defaultdict(float)
        self.llm_usage: dict[str, Any] = {
            "total": self._empty_llm_bucket(),
            "by_model": {},
            "by_component": {},
            "by_phase": {},
        }
        self.methods: dict[str, JsonDict] = {}

        os.makedirs(output_path, exist_ok=True)
        self.record_event("run_started", config_snapshot=self.config_snapshot)

    @staticmethod
    def _empty_llm_bucket() -> LLMBucket:
        """
        Return a fresh aggregate bucket for LLM usage counters.

        Buckets are used for totals and for model/component/phase groupings.
        ``estimated_cost_usd`` is present but left ``None`` because pricing is
        provider- and time-dependent.

        :return: New LLM usage aggregate dictionary.
        """
        return {
            "call_count": 0,
            "failed_call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "elapsed_seconds": 0.0,
            "estimated_cost_usd": None,
        }

    @staticmethod
    def _add_llm_usage(bucket: LLMBucket, event: JsonDict) -> None:
        """
        Add one ``llm_call`` event's counters to an aggregate bucket.

        :param bucket: Mutable LLM aggregate bucket to update.
        :param event: Metrics event dictionary, expected to represent an
            ``llm_call`` event.
        :return: ``None``.
        """
        bucket["call_count"] += 1
        if event.get("success") is False:
            bucket["failed_call_count"] += 1
        bucket["input_tokens"] += int(event.get("input_tokens") or 0)
        bucket["output_tokens"] += int(event.get("output_tokens") or 0)
        bucket["total_tokens"] += int(event.get("total_tokens") or 0)
        bucket["elapsed_seconds"] += float(event.get("elapsed_seconds") or 0.0)

    @staticmethod
    def _bucket_for(mapping: dict[str, LLMBucket], key: Any) -> LLMBucket:
        """
        Return the aggregate bucket for ``key``, creating it when needed.

        Empty or missing grouping keys are normalized to ``"unknown"``.

        :param mapping: Mutable mapping from grouping key to LLM bucket.
        :param key: Grouping key, such as model, component, or phase.
        :return: Existing or newly created LLM usage bucket.
        """
        bucket_key: str = str(key) if key not in (None, "") else "unknown"
        if bucket_key not in mapping:
            mapping[bucket_key] = MetricsRecorder._empty_llm_bucket()
        return mapping[bucket_key]

    def _update_summary_state(self, event: JsonDict) -> None:
        """
        Fold a single event into the in-memory summary aggregates.

        This updates event counts, failure counts, elapsed-time totals, LLM usage
        totals, and per-sample method summaries.

        :param event: Complete metrics event dictionary.
        :return: ``None``.
        """
        event_type: str = event.get("event_type", "unknown")
        self.events_by_type[event_type] += 1
        if event.get("success") is False:
            self.failures_by_type[event_type] += 1

        elapsed: Any | None = event.get("elapsed_seconds")
        if elapsed is not None:
            self.elapsed_by_type[event_type] += float(elapsed)

        if event_type == "llm_call":
            self._add_llm_usage(self.llm_usage["total"], event)
            self._add_llm_usage(self._bucket_for(self.llm_usage["by_model"], event.get("model")), event)
            self._add_llm_usage(self._bucket_for(self.llm_usage["by_component"], event.get("component")), event)
            self._add_llm_usage(self._bucket_for(self.llm_usage["by_phase"], event.get("phase")), event)

        sample_id: Any | None = event.get("sample_id")
        if sample_id is not None:
            key: str = str(sample_id)
            if key not in self.methods:
                self.methods[key] = {
                    "sample_id": sample_id,
                    "method_name": event.get("method_name"),
                    "event_count": 0,
                    "elapsed_seconds": 0.0,
                    "elapsed_by_type": {},
                    "llm": self._empty_llm_bucket(),
                }
            method: JsonDict = self.methods[key]
            if method.get("method_name") is None and event.get("method_name"):
                method["method_name"] = event.get("method_name")
            method["event_count"] += 1
            if elapsed is not None:
                elapsed_float: float = float(elapsed)
                if event_type == "analysis_method":
                    method["elapsed_seconds"] += elapsed_float
                method["elapsed_by_type"][event_type] = method["elapsed_by_type"].get(event_type, 0.0) + elapsed_float
            if event_type == "llm_call":
                self._add_llm_usage(method["llm"], event)

    def record_event(self, event_type: str, **fields: Any) -> None:
        """
        Write one metrics event and update in-memory aggregates.

        The active metric context is merged first, then explicit ``fields``
        override context values. ``None`` fields are omitted from the final event.
        Events are appended as one JSON object per line to
        ``metrics.events.jsonl``.

        :param event_type: Event name stored in the ``event_type`` field.
        :param fields: Event-specific metadata fields.
        :return: ``None``.
        """
        context: JsonDict = get_metric_context()
        event: JsonDict = {
            "timestamp": utc_now_iso(),
            "run_id": self.run_id,
            "event_type": event_type,
        }
        event.update(context)
        event.update({k: _jsonable(v) for k, v in fields.items() if v is not None})

        self.event_count += 1
        self._update_summary_state(event)

        try:
            with open(self.events_path, "a") as metrics_file:
                metrics_file.write(json.dumps(event, sort_keys=True) + "\n")
        except Exception as exc:
            self.write_failures += 1
            if self.write_failures == 1:
                print(f"Could not write metrics event: {exc}")

    def build_summary(self) -> JsonDict:
        """
        Build the current run summary without writing it to disk.

        The summary contains run metadata, event counts, timing aggregates, LLM
        usage grouped by model/component/phase, and per-sample method metrics.

        :return: Summary dictionary ready to serialize as JSON.
        """
        finished_at: str = utc_now_iso()
        total_elapsed: float = time.perf_counter() - self.started_perf
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "elapsed_seconds": total_elapsed,
            "config_snapshot": self.config_snapshot,
            "events": {
                "total": self.event_count,
                "by_type": dict(self.events_by_type),
                "failures_by_type": dict(self.failures_by_type),
                "metrics_write_failures": self.write_failures,
            },
            "timing_seconds": {
                "by_event_type": dict(self.elapsed_by_type),
            },
            "llm_usage": self.llm_usage,
            "methods": self.methods,
        }

    def write_summary(self) -> JsonDict:
        """
        Write ``metrics.summary.json`` and return the summary dictionary.

        Like event writes, summary write failures are reported but do not raise,
        preserving the main Cascade execution behavior.

        :return: Summary dictionary, regardless of whether writing succeeded.
        """
        summary: JsonDict = self.build_summary()
        try:
            with open(self.summary_path, "w") as summary_file:
                json.dump(summary, summary_file, indent=2, sort_keys=True)
        except Exception as exc:
            self.write_failures += 1
            print(f"Could not write metrics summary: {exc}")
        return summary
