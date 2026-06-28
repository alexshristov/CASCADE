"""
Runtime metrics utilities for Cascade.

The module provides a small, dependency-free event recorder. It is intentionally
file-based so a run can be inspected after a crash and so metrics do not depend
on an external observability service. Events are written as JSON Lines and kept
in memory only for the compact summary produced at the end of a run.
"""

from __future__ import annotations
import json
import time
import uuid
from contextlib import ContextDecorator
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from types import TracebackType
from typing import Any

from cascade.metrics.BaseMetricsRecorder import BaseMetricsRecorder, JsonDict


_current_recorder: ContextVar[BaseMetricsRecorder | None] = ContextVar("cascade_metrics_recorder", default=None)
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


def set_current_recorder(recorder: BaseMetricsRecorder) -> Token[BaseMetricsRecorder | None]:
    """
    Install ``recorder`` as the current metrics sink for this context.

    Returns a contextvars token that must be passed to
    :func:`reset_current_recorder` when the run is finished.

    :param recorder: :class:`BaseMetricsRecorder` instance to make active.
    :return: Context variable token for later reset.
    """
    return _current_recorder.set(recorder)


def reset_current_recorder(token: Token[BaseMetricsRecorder | None]) -> None:
    """
    Restore the previous current recorder using a token from
    :func:`set_current_recorder`.

    :param token: Context variable token returned by
        :func:`set_current_recorder`.
    :return: ``None``.
    """
    _current_recorder.reset(token)


def get_current_recorder() -> BaseMetricsRecorder | None:
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
    recorder: BaseMetricsRecorder | None = get_current_recorder()
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
