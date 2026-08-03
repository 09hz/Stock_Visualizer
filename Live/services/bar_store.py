from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Optional
from uuid import uuid4

import pandas as pd


class BarStore:
    """
    Disk-backed OHLCV replay cache.

    Memory cache disappears when the app stops.
    This store writes parquet files so replay history survives:
    - closing browser tabs
    - stopping/restarting Dash
    - restarting the computer
    """

    def __init__(self, root_dir: str | Path = "cache/replay"):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def _safe_symbol(self, symbol: str) -> str:
        return (symbol or "").upper().strip().replace("/", "_")

    def _safe_timeframe(self, timeframe: str) -> str:
        return (timeframe or "1 min").replace(" ", "_").replace("/", "_")

    def _safe_date(self, replay_date: Optional[str]) -> str:
        return replay_date or "latest"

    def path_for(self, symbol: str, timeframe: str, replay_date: Optional[str]) -> Path:
        symbol = self._safe_symbol(symbol)
        timeframe = self._safe_timeframe(timeframe)
        replay_date = self._safe_date(replay_date)
        return self.root_dir / symbol / timeframe / f"{replay_date}.parquet"

    def read(self, symbol: str, timeframe: str, replay_date: Optional[str]) -> Optional[pd.DataFrame]:
        path = self.path_for(symbol, timeframe, replay_date)

        try:
            with self._lock:
                if not path.exists():
                    return None
                df = pd.read_parquet(path)
            if df is None or df.empty:
                return None
            return df
        except Exception as exc:
            print(f"[BAR STORE READ ERROR] {path}: {exc}", flush=True)
            return None

    def write(self, symbol: str, timeframe: str, replay_date: Optional[str], df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return

        path = self.path_for(symbol, timeframe, replay_date)

        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
                try:
                    df.to_parquet(temp_path, index=False)
                    temp_path.replace(path)
                finally:
                    if temp_path.exists():
                        temp_path.unlink()
            print(f"[BAR STORE WRITE] {path}", flush=True)
        except Exception as exc:
            print(f"[BAR STORE WRITE ERROR] {path}: {exc}", flush=True)

    def delete(self, symbol: str, timeframe: Optional[str] = None, replay_date: Optional[str] = None) -> None:
        symbol = self._safe_symbol(symbol)

        if timeframe and replay_date:
            path = self.path_for(symbol, timeframe, replay_date)
            if path.exists():
                path.unlink()
            return

        symbol_dir = self.root_dir / symbol
        if not symbol_dir.exists():
            return

        for path in symbol_dir.rglob("*.parquet"):
            try:
                path.unlink()
            except OSError:
                pass

    def clear_all(self) -> None:
        if not self.root_dir.exists():
            return

        for path in self.root_dir.rglob("*.parquet"):
            try:
                path.unlink()
            except OSError:
                pass
