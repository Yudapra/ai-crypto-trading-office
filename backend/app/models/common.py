"""Shared value types for security analysis.

All timestamps are timezone-aware UTC. Nothing here fetches data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class Decision(str, Enum):
    PASS = "PASS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class DataLabel(str, Enum):
    LIVE = "LIVE"
    STALE = "STALE"
    MOCK = "MOCK"
    UNKNOWN = "UNKNOWN"


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class CheckResult(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_stale(ts: Optional[datetime], now: datetime, max_age_s: int) -> bool:
    """Missing timestamps and timestamps from the future are treated as stale."""
    if ts is None:
        return True
    age = (now - ts).total_seconds()
    return age < -5 or age > max_age_s


def effective_label(label: DataLabel, ts: Optional[datetime], now: datetime, max_age_s: int) -> DataLabel:
    if label in (DataLabel.MOCK, DataLabel.UNKNOWN):
        return label
    return DataLabel.STALE if is_stale(ts, now, max_age_s) else DataLabel.LIVE


@dataclass(frozen=True)
class Finding:
    """One structured security finding."""

    check: str
    result: CheckResult
    severity: Severity
    evidence: str
    data_timestamp: Optional[datetime]
    limitations: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check,
            "result": self.result.value,
            "severity": self.severity.value,
            "evidence": self.evidence,
            "data_timestamp": self.data_timestamp.isoformat() if self.data_timestamp else None,
            "limitations": self.limitations,
        }
