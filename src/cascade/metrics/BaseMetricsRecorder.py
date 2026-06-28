from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

JsonDict = dict[str, Any]


class BaseMetricsRecorder(ABC):
    """
    Abstract interface for Cascade metrics recorders.

    Any recorder installed as the active sink must implement these two methods.
    The module-level ``record_event`` helper calls ``record_event`` on the active
    recorder; ``Pipeline.execute`` calls ``write_summary`` at the end of a run.
    """

    @abstractmethod
    def record_event(self, event_type: str, **fields: Any) -> None:
        """
        Persist one metrics event.

        :param event_type: Event name used for grouping in summaries.
        :param fields: Event-specific metadata fields.
        :return: ``None``.
        """

    @abstractmethod
    def write_summary(self) -> JsonDict:
        """
        Finalize and persist the run summary.

        :return: Summary dictionary, regardless of whether writing succeeded.
        """
