from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re

from .exceptions import InitError


@dataclass(frozen=True, slots=True)
class DateBoundary:
    start: date | None = None
    end: date | None = None

    def __post_init__(self) -> None:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise InitError("--date-boundary start must be <= end")

    def serialize(self) -> str | None:
        if self.start is None and self.end is None:
            return None
        start = self.start.isoformat() if self.start is not None else ""
        end = self.end.isoformat() if self.end is not None else ""
        return f"{start}:{end}"

    def contains(self, value: datetime) -> bool:
        day = value.date()
        if self.start is not None and day < self.start:
            return False
        if self.end is not None and day > self.end:
            return False
        return True


@dataclass(frozen=True, slots=True)
class IdBoundary:
    start: int | None = None
    end: int | None = None

    def __post_init__(self) -> None:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise InitError("--id-boundary start must be <= end")

    def serialize(self) -> str | None:
        if self.start is None and self.end is None:
            return None
        start = str(self.start) if self.start is not None else ""
        end = str(self.end) if self.end is not None else ""
        return f"{start}:{end}"

    def contains(self, mid: str) -> bool:
        value = parse_mid_value(mid)
        if value is None:
            return False
        if self.start is not None and value < self.start:
            return False
        if self.end is not None and value > self.end:
            return False
        return True


def parse_date_boundary(raw: str | None) -> DateBoundary | None:
    parts = _split_boundary(raw, flag_name="--date-boundary")
    if parts is None:
        return None
    start_raw, end_raw = parts
    return DateBoundary(
        start=_parse_date_endpoint(start_raw, field_name="--date-boundary start"),
        end=_parse_date_endpoint(end_raw, field_name="--date-boundary end"),
    )


def parse_id_boundary(raw: str | None) -> IdBoundary | None:
    parts = _split_boundary(raw, flag_name="--id-boundary")
    if parts is None:
        return None
    start_raw, end_raw = parts
    return IdBoundary(
        start=_parse_id_endpoint(start_raw, field_name="--id-boundary start"),
        end=_parse_id_endpoint(end_raw, field_name="--id-boundary end"),
    )


def serialize_boundary(boundary: DateBoundary | IdBoundary | None) -> str | None:
    if boundary is None:
        return None
    return boundary.serialize()


def parse_mid_value(mid: str) -> int | None:
    try:
        return _parse_id_endpoint(mid, field_name="post MID")
    except InitError:
        return None


def _split_boundary(raw: str | None, *, flag_name: str) -> tuple[str, str] | None:
    if raw is None:
        return None
    text = raw.strip()
    if text.count(":") != 1:
        raise InitError(f"{flag_name} must use inclusive START:END syntax")
    start, end = text.split(":", 1)
    if not start and not end:
        return None
    return start, end


def _parse_date_endpoint(value: str, *, field_name: str) -> date | None:
    if not value:
        return None
    formats = (
        (r"\d{8}", "%Y%m%d"),
        (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
    )
    for pattern, fmt in formats:
        if not re.fullmatch(pattern, value):
            continue
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            break
    raise InitError(f"{field_name} must be YYYYMMDD or YYYY-MM-DD")


def _parse_id_endpoint(value: str, *, field_name: str) -> int | None:
    if not value:
        return None
    if not value.isdecimal():
        raise InitError(f"{field_name} must be a non-negative decimal integer")
    return int(value)
