from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Any, Callable


@dataclass
class SessionStateEntry:
    services: Any
    last_access: float


class SessionStateRegistry:
    """Thread-safe, idle-expiring service bundles keyed by browser session."""

    def __init__(
        self,
        factory: Callable[[str], Any],
        *,
        idle_ttl_seconds: int = 4 * 60 * 60,
    ):
        self.factory = factory
        self.idle_ttl_seconds = max(60, int(idle_ttl_seconds))
        self._lock = RLock()
        self._entries: dict[str, SessionStateEntry] = {}

    def get(self, session_id: str):
        session_id = str(session_id or "").strip()
        if not session_id:
            raise RuntimeError("Missing browser session identifier.")

        now = monotonic()
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None:
                entry = SessionStateEntry(
                    services=self.factory(session_id),
                    last_access=now,
                )
                self._entries[session_id] = entry
            else:
                entry.last_access = now

            self._remove_idle_locked(now, exclude=session_id)
            return entry.services

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._entries.pop(str(session_id or ""), None)

    def active_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def _remove_idle_locked(self, now: float, *, exclude: str | None = None) -> None:
        expired = [
            session_id
            for session_id, entry in self._entries.items()
            if session_id != exclude
            and now - entry.last_access > self.idle_ttl_seconds
        ]
        for session_id in expired:
            self._entries.pop(session_id, None)


class SessionServiceProxy:
    """Resolve one named service from the current browser's service bundle."""

    def __init__(
        self,
        registry: SessionStateRegistry,
        session_id_resolver: Callable[[], str],
        attribute: str,
    ):
        object.__setattr__(self, "_registry", registry)
        object.__setattr__(self, "_session_id_resolver", session_id_resolver)
        object.__setattr__(self, "_attribute", attribute)

    def _service(self):
        bundle = self._registry.get(self._session_id_resolver())
        return getattr(bundle, self._attribute)

    def __getattr__(self, name: str):
        return getattr(self._service(), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(self._service(), name, value)
