from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd


@dataclass(frozen=True)
class MarketStatus:
    is_open: bool
    reason: str
    exchange_now: datetime
    session_date: date | None
    session_open: datetime | None
    session_close: datetime | None
    last_completed_session: date
    next_open: datetime


class MarketCalendarService:
    """Authoritative NYSE regular-session calendar and Live-mode guard."""

    def __init__(
        self,
        calendar_name: str = "XNYS",
        exchange_timezone: str = "America/New_York",
    ):
        self.calendar = xcals.get_calendar(calendar_name)
        self.exchange_tz = ZoneInfo(exchange_timezone)

    def exchange_now(self, now: datetime | pd.Timestamp | None = None) -> datetime:
        if now is None:
            return datetime.now(self.exchange_tz)

        timestamp = pd.Timestamp(now)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(self.exchange_tz)
        else:
            timestamp = timestamp.tz_convert(self.exchange_tz)
        return timestamp.to_pydatetime()

    @staticmethod
    def _date_value(value) -> date:
        timestamp = pd.to_datetime(value, errors="coerce")
        if pd.isna(timestamp):
            raise ValueError(f"Invalid market date: {value}")
        return timestamp.date()

    def is_session(self, value) -> bool:
        return bool(self.calendar.is_session(self._date_value(value).isoformat()))

    def sessions_in_range(self, start_date, end_date) -> list[str]:
        start = self._date_value(start_date)
        end = self._date_value(end_date)
        if end < start:
            start, end = end, start

        sessions = self.calendar.sessions_in_range(
            start.isoformat(),
            end.isoformat(),
        )
        return [pd.Timestamp(session).date().isoformat() for session in sessions]

    def session_bounds(self, session_date) -> tuple[datetime, datetime]:
        session = self.calendar.date_to_session(
            self._date_value(session_date).isoformat(),
            direction="none",
        )
        session_open = self.calendar.session_open(session)
        session_close = self.calendar.session_close(session)
        return (
            session_open.tz_convert(self.exchange_tz).to_pydatetime(),
            session_close.tz_convert(self.exchange_tz).to_pydatetime(),
        )

    def session_on_or_before(self, value) -> date:
        selected = self._date_value(value)
        session = self.calendar.date_to_session(
            selected.isoformat(),
            direction="previous",
        )
        return pd.Timestamp(session).date()

    def previous_session(self, value) -> date:
        selected = self._date_value(value)
        if self.is_session(selected):
            session = self.calendar.previous_session(selected.isoformat())
            return pd.Timestamp(session).date()
        return self.session_on_or_before(selected)

    def next_session_open(self, value) -> datetime:
        selected = self._date_value(value)
        if self.is_session(selected):
            session = self.calendar.next_session(selected.isoformat())
        else:
            session = self.calendar.date_to_session(
                selected.isoformat(),
                direction="next",
            )
        return (
            self.calendar.session_open(session)
            .tz_convert(self.exchange_tz)
            .to_pydatetime()
        )

    def last_completed_session(
        self,
        now: datetime | pd.Timestamp | None = None,
    ) -> date:
        exchange_now = self.exchange_now(now)
        today = exchange_now.date()

        if self.is_session(today):
            _session_open, session_close = self.session_bounds(today)
            if exchange_now >= session_close:
                return today
            return self.previous_session(today)

        return self.session_on_or_before(today)

    def status(self, now: datetime | pd.Timestamp | None = None) -> MarketStatus:
        exchange_now = self.exchange_now(now)
        today = exchange_now.date()
        last_completed = self.last_completed_session(exchange_now)

        if self.is_session(today):
            session_open, session_close = self.session_bounds(today)
            if session_open <= exchange_now < session_close:
                return MarketStatus(
                    is_open=True,
                    reason="regular session is open",
                    exchange_now=exchange_now,
                    session_date=today,
                    session_open=session_open,
                    session_close=session_close,
                    last_completed_session=last_completed,
                    next_open=session_open,
                )

            if exchange_now < session_open:
                reason = "market has not opened yet"
                next_open = session_open
            else:
                reason = "market is closed for the day"
                next_open = self.next_session_open(today)

            return MarketStatus(
                is_open=False,
                reason=reason,
                exchange_now=exchange_now,
                session_date=today,
                session_open=session_open,
                session_close=session_close,
                last_completed_session=last_completed,
                next_open=next_open,
            )

        return MarketStatus(
            is_open=False,
            reason="weekend or exchange holiday",
            exchange_now=exchange_now,
            session_date=None,
            session_open=None,
            session_close=None,
            last_completed_session=last_completed,
            next_open=self.next_session_open(today),
        )

    def live_access(
        self,
        replay_date=None,
        now: datetime | pd.Timestamp | None = None,
    ) -> tuple[bool, str, MarketStatus]:
        status = self.status(now)
        if replay_date:
            selected = self._date_value(replay_date)
            if selected != status.exchange_now.date():
                return False, "Live mode is unavailable for historical dates.", status

        if not status.is_open:
            next_open = status.next_open.strftime("%A, %b %d at %I:%M %p ET")
            return (
                False,
                f"Market closed ({status.reason}). Live mode resumes {next_open}.",
                status,
            )

        return True, "NYSE regular session is open.", status

    def is_quote_fresh(
        self,
        updated_at,
        *,
        max_age_seconds: int = 60,
        now: datetime | pd.Timestamp | None = None,
    ) -> bool:
        if updated_at is None:
            return False

        quote_time = pd.Timestamp(updated_at)
        if quote_time.tzinfo is None:
            quote_time = quote_time.tz_localize(self.exchange_tz)
        else:
            quote_time = quote_time.tz_convert(self.exchange_tz)

        current_time = pd.Timestamp(self.exchange_now(now))
        age_seconds = (current_time - quote_time).total_seconds()
        return 0 <= age_seconds <= max(1, int(max_age_seconds))

    def non_session_days(
        self,
        start_date=None,
        end_date=None,
    ) -> list[str]:
        exchange_today = self.exchange_now().date()
        start = self._date_value(start_date or exchange_today - timedelta(days=3650))
        end = self._date_value(end_date or exchange_today + timedelta(days=365))
        sessions = set(self.sessions_in_range(start, end))

        disabled = []
        current = start
        while current <= end:
            if current.isoformat() not in sessions:
                disabled.append(current.isoformat())
            current += timedelta(days=1)
        return disabled
