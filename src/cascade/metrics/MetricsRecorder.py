from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from typing import Any

from cascade.metrics.BaseMetricsRecorder import BaseMetricsRecorder, JsonDict
from cascade.utils.Metrics import _jsonable, get_metric_context, make_run_id, utc_now_iso

LLMBucket = dict[str, Any]


class MetricsRecorder(BaseMetricsRecorder):
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
