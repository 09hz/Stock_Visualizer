from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import plotly.graph_objects as go


@dataclass
class StrategySignal:
    index: int
    time: Any
    side: str
    price: float
    rule: str


@dataclass
class StrategyBackground:
    start_index: int
    end_index: int
    start_time: Any
    end_time: Any
    color: str
    label: str
    rule: str


@dataclass
class StrategyScriptResult:
    lines: dict[str, pd.Series] = field(default_factory=dict)
    plots: list[str] = field(default_factory=list)
    signals: list[StrategySignal] = field(default_factory=list)
    backgrounds: list[StrategyBackground] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class StrategyEngine:
    """
    Safe Pine-inspired Strategy Lab engine.

    Strategy Language v0.4 supports:
        - ta.* aliases
        - indicator assignments:
              fast = ta.ema(close, 9)
              r = ta.rsi(close, 14)
              atr = ta.atr(close, 14)
        - boolean condition assignments:
              bullCross = ta.crossover(fast, slow)
              aboveTrend = close > trend
              longSignal = bullCross and aboveTrend
        - session/time filters:
              inSession = session("0930-1600")
              morning = time_hhmm >= 930 and time_hhmm <= 1130
              weekday = dayofweek >= 0 and dayofweek <= 4
        - buy/sell conditions:
              buy when longSignal
              sell when bearCross or r > 80
        - background regime shading:
              bgcolor bullMarket color="green"
              bgcolor bearMarket color="red"

    Safety:
        No eval.
        No exec.
        No imports.
        No raw Python execution.
        Expressions are parsed with Python AST and only a small whitelist
        of nodes/functions is evaluated.
    """

    NAME_PATTERN = r"[A-Za-z_][A-Za-z0-9_]*"
    FUNC_PATTERN = r"[A-Za-z_][A-Za-z0-9_\.]*"

    ASSIGN_RE = re.compile(
        rf"^(?P<name>{NAME_PATTERN})\s*=\s*"
        rf"(?P<func>{FUNC_PATTERN})\("
        rf"(?P<source>{NAME_PATTERN})\s*,\s*"
        r"(?P<length>\d+)\s*\)\s*$",
        flags=re.IGNORECASE,
    )

    PLOT_RE = re.compile(
        rf"^plot\s+(?P<name>{NAME_PATTERN})\s*$",
        flags=re.IGNORECASE,
    )

    SIGNAL_RE = re.compile(
        r"^(?P<side>buy|sell)\s+when\s+(?P<expr>.+?)\s*$",
        flags=re.IGNORECASE,
    )

    BGCOLOR_RE = re.compile(
        r"^bgcolor\s+(?P<expr>.+?)\s+color\s*=\s*[\"']?(?P<color>[A-Za-z_][A-Za-z0-9_#-]*)[\"']?\s*$",
        flags=re.IGNORECASE,
    )

    BLOCKED_WORDS = {
        "import",
        "exec",
        "eval",
        "open",
        "os",
        "sys",
        "subprocess",
        "socket",
        "requests",
        "class",
        "def",
        "lambda",
        "while",
        "for",
        "with",
        "try",
        "except",
        "raise",
        "return",
        "globals",
        "locals",
        "compile",
        "input",
        "print",
        "setattr",
        "getattr",
        "delattr",
        "__builtins__",
        "__import__",
    }

    FUNCTION_ALIASES = {
        "ta.sma": "sma",
        "ta.ema": "ema",
        "ta.rsi": "rsi",
        "ta.highest": "highest",
        "ta.lowest": "lowest",
        "ta.atr": "atr",
        "ta.crossover": "crossover",
        "ta.crossunder": "crossunder",
    }

    SUPPORTED_INDICATORS = {"sma", "ema", "rsi", "highest", "lowest", "atr"}
    SUPPORTED_CONDITION_FUNCTIONS = {"crossover", "crossunder"}

    # Rendering safeguards. These prevent Plotly/Dash from freezing when a
    # strategy produces too many markers or overlay lines during replay.
    MAX_RENDERED_STRATEGY_LINES = 4
    MAX_RENDERED_STRATEGY_SIGNALS = 150
    MAX_RENDERED_BACKGROUNDS = 20

    # Signal-generation safeguard. In position-aware mode, repeated true
    # conditions only create one BUY while flat and one SELL while long.
    POSITION_AWARE_SIGNALS = True

    def run(self, script: str, bars: pd.DataFrame) -> StrategyScriptResult:
        result = StrategyScriptResult()

        if bars is None or bars.empty:
            result.errors.append("No bars available.")
            return result

        clean_bars = self._clean_bars(bars)

        if clean_bars.empty:
            result.errors.append("No valid bars available.")
            return result

        series_context = self._build_series_context(clean_bars)
        conditions: dict[str, pd.Series] = {}
        signal_rules: list[tuple[str, str, str]] = []
        background_rules: list[tuple[str, str, str]] = []

        for raw_line in str(script or "").splitlines():
            line = str(raw_line or "").strip()

            if not line or line.startswith("#") or line.startswith("//"):
                continue

            line = self._strip_inline_comment(line).strip()
            if not line:
                continue

            line = self._normalize_strategy_aliases(line)

            unsafe = self._find_blocked_token(line)
            if unsafe:
                result.errors.append(f"Blocked unsafe token '{unsafe}' in line: {line}")
                continue

            assign_match = self.ASSIGN_RE.match(line)
            if assign_match:
                func_name = self._normalize_function_name(assign_match.group("func"))

                # Crossover/crossunder with numeric thresholds also match ASSIGN_RE
                # syntactically, for example:
                #     rsiRecover = crossover(rsiSlow, 35)
                # Treat only supported indicators as indicator assignments.
                # Let all other assignments fall through to the expression parser.
                if func_name in self.SUPPORTED_INDICATORS:
                    self._handle_assignment(
                        assign_match,
                        clean_bars,
                        series_context,
                        result,
                        line,
                    )
                    continue

            expr_assignment = self._match_expression_assignment(line)
            if expr_assignment is not None:
                name, expr = expr_assignment
                condition = self._evaluate_condition_expression(
                    expr=expr,
                    bars=clean_bars,
                    series_context=series_context,
                    conditions=conditions,
                )
                condition = self._to_bool_series(condition, clean_bars)

                if condition is None:
                    result.errors.append(f"Could not parse condition assignment: {line}")
                    continue

                conditions[name] = condition
                continue

            plot_match = self.PLOT_RE.match(line)
            if plot_match:
                self._handle_plot(plot_match, result)
                continue

            bgcolor_match = self.BGCOLOR_RE.match(line)
            if bgcolor_match:
                expr = bgcolor_match.group("expr").strip()
                color = bgcolor_match.group("color").strip()
                background_rules.append((expr, color, line))
                continue

            signal_match = self.SIGNAL_RE.match(line)
            if signal_match:
                side = signal_match.group("side").upper().strip()
                expr = signal_match.group("expr").strip()
                signal_rules.append((side, expr, line))
                continue

            result.errors.append(f"Could not parse line: {line}")

        self._build_backgrounds_from_rules(
            background_rules=background_rules,
            bars=clean_bars,
            series_context=series_context,
            conditions=conditions,
            result=result,
        )

        self._build_signals_from_rules(
            signal_rules=signal_rules,
            bars=clean_bars,
            series_context=series_context,
            conditions=conditions,
            result=result,
        )

        return result


    def filter_result_to_bars(
        self,
        result: StrategyScriptResult,
        source_bars: pd.DataFrame,
        target_bars: pd.DataFrame,
    ) -> StrategyScriptResult:
        """
        Return a lightweight StrategyScriptResult aligned to target_bars.

        This lets Dash calculate strategy lines/signals once on the full loaded
        dataset, then during replay render only the current visible/cursor
        window without recalculating every indicator and condition each tick.
        """

        if result is None:
            return StrategyScriptResult()

        if source_bars is None or source_bars.empty or target_bars is None or target_bars.empty:
            return StrategyScriptResult(
                errors=list(result.errors or []),
            )

        try:
            source = self._clean_bars(source_bars)
            target = self._clean_bars(target_bars)
        except Exception:
            return result

        if source.empty or target.empty or "time" not in source.columns or "time" not in target.columns:
            return result

        target_times = pd.to_datetime(target["time"], errors="coerce")
        target_min = target_times.min()
        target_max = target_times.max()

        if pd.isna(target_min) or pd.isna(target_max):
            return result

        filtered = StrategyScriptResult(
            lines={},
            plots=list(result.plots or []),
            signals=[],
            backgrounds=[],
            errors=list(result.errors or []),
        )

        source_times = pd.to_datetime(source["time"], errors="coerce")
        target_frame = pd.DataFrame(
            {
                "time": target_times,
                "_target_index": target.index,
            }
        )

        for name, series in dict(result.lines or {}).items():
            try:
                values = pd.Series(series).reindex(source.index)
                line_frame = pd.DataFrame(
                    {
                        "time": source_times,
                        "_value": values,
                    }
                ).dropna(subset=["time"])

                merged = target_frame.merge(line_frame, on="time", how="left")
                filtered.lines[name] = pd.Series(
                    merged["_value"].values,
                    index=target.index,
                    name=name,
                )
            except Exception:
                try:
                    filtered.lines[name] = pd.Series(series).reindex(target.index)
                except Exception:
                    pass

        for sig in list(result.signals or []):
            try:
                sig_time = pd.to_datetime(sig.time, errors="coerce")
                if pd.notna(sig_time) and target_min <= sig_time <= target_max:
                    filtered.signals.append(sig)
            except Exception:
                continue

        for bg in list(getattr(result, "backgrounds", []) or []):
            try:
                bg_start = pd.to_datetime(bg.start_time, errors="coerce")
                bg_end = pd.to_datetime(bg.end_time, errors="coerce")
                if pd.isna(bg_start) or pd.isna(bg_end):
                    continue

                # Keep ranges that overlap the current target window.
                if bg_end >= target_min and bg_start <= target_max:
                    filtered.backgrounds.append(bg)
            except Exception:
                continue

        return filtered


    def add_backgrounds_to_figure(
        self,
        fig: go.Figure,
        bars: pd.DataFrame,
        result: StrategyScriptResult,
    ) -> go.Figure:
        """
        Add TradingView-style regime background shading.

        Background ranges are merged when the script is parsed, then capped here
        so replay remains responsive.
        """
        if bars is None or bars.empty or result is None:
            return fig

        backgrounds = list(getattr(result, "backgrounds", []) or [])
        if not backgrounds:
            return fig

        if len(backgrounds) > self.MAX_RENDERED_BACKGROUNDS:
            backgrounds = backgrounds[-self.MAX_RENDERED_BACKGROUNDS:]

        for bg in backgrounds:
            color = self._background_fill_color(bg.color)

            try:
                fig.add_vrect(
                    x0=bg.start_time,
                    x1=bg.end_time,
                    fillcolor=color,
                    opacity=1.0,
                    line_width=0,
                    layer="below",
                    annotation_text=None,
                )
            except Exception:
                continue

        return fig

    def add_plots_to_figure(
        self,
        fig: go.Figure,
        bars: pd.DataFrame,
        result: StrategyScriptResult,
    ) -> go.Figure:
        if bars is None or bars.empty or result is None:
            return fig

        clean_bars = self._clean_bars(bars)

        if clean_bars.empty:
            return fig

        x_values = clean_bars["time"] if "time" in clean_bars.columns else clean_bars.index

        for name in list(result.plots or [])[: self.MAX_RENDERED_STRATEGY_LINES]:
            series = result.lines.get(name)

            if series is None:
                continue

            try:
                y_values = series.reindex(clean_bars.index)
            except Exception:
                y_values = series

            fig.add_trace(
                go.Scatter(
                    x=x_values,
                    y=y_values,
                    mode="lines",
                    name=name,
                    line={"width": 1.6},
                    hovertemplate=f"{name}: %{{y:.4f}}<extra></extra>",
                )
            )

        return fig

    def add_signals_to_figure(
        self,
        fig: go.Figure,
        result: StrategyScriptResult,
    ) -> go.Figure:
        if result is None or not result.signals:
            return fig

        signals = list(result.signals or [])

        if len(signals) > self.MAX_RENDERED_STRATEGY_SIGNALS:
            signals = signals[-self.MAX_RENDERED_STRATEGY_SIGNALS:]

        buys = [sig for sig in signals if str(sig.side).upper() == "BUY"]
        sells = [sig for sig in signals if str(sig.side).upper() == "SELL"]

        if buys:
            fig.add_trace(
                go.Scatter(
                    x=[sig.time if sig.time is not None else sig.index for sig in buys],
                    y=[sig.price for sig in buys],
                    mode="markers+text",
                    name="Strategy BUY",
                    text=["BUY"] * len(buys),
                    textposition="bottom center",
                    marker={
                        "symbol": "triangle-up",
                        "size": 16,
                        "color": "#22c55e",
                        "line": {"width": 1, "color": "#ffffff"},
                    },
                    cliponaxis=False,
                    hovertemplate=(
                        "BUY<br>"
                        "Time: %{x}<br>"
                        "Price: %{y:.4f}"
                        "<extra></extra>"
                    ),
                )
            )

        if sells:
            fig.add_trace(
                go.Scatter(
                    x=[sig.time if sig.time is not None else sig.index for sig in sells],
                    y=[sig.price for sig in sells],
                    mode="markers+text",
                    name="Strategy SELL",
                    text=["SELL"] * len(sells),
                    textposition="top center",
                    marker={
                        "symbol": "triangle-down",
                        "size": 16,
                        "color": "#ef4444",
                        "line": {"width": 1, "color": "#ffffff"},
                    },
                    cliponaxis=False,
                    hovertemplate=(
                        "SELL<br>"
                        "Time: %{x}<br>"
                        "Price: %{y:.4f}"
                        "<extra></extra>"
                    ),
                )
            )

        return fig

    def _match_expression_assignment(self, line: str) -> tuple[str, str] | None:
        match = re.match(
            rf"^(?P<name>{self.NAME_PATTERN})\s*=\s*(?P<expr>.+?)\s*$",
            str(line or ""),
            flags=re.IGNORECASE,
        )
        if not match:
            return None

        name = match.group("name").strip()
        expr = match.group("expr").strip()

        if not name or not expr:
            return None

        return name, expr

    def _build_series_context(self, bars: pd.DataFrame) -> dict[str, pd.Series]:
        context: dict[str, pd.Series] = {}

        for col in ["open", "high", "low", "close", "volume"]:
            if col in bars.columns:
                context[col] = pd.to_numeric(bars[col], errors="coerce")

        if "time" in bars.columns:
            times = pd.to_datetime(bars["time"], errors="coerce")

            context["hour"] = pd.Series(times.dt.hour, index=bars.index)
            context["minute"] = pd.Series(times.dt.minute, index=bars.index)
            context["dayofweek"] = pd.Series(times.dt.dayofweek, index=bars.index)
            context["time_hhmm"] = pd.Series(
                times.dt.hour * 100 + times.dt.minute,
                index=bars.index,
            )

        return context

    def _clean_bars(self, bars: pd.DataFrame) -> pd.DataFrame:
        required = ["open", "high", "low", "close", "volume"]

        if bars is None or bars.empty:
            return pd.DataFrame(columns=["time", *required])

        df = bars.copy()

        if "time" in df.columns:
            df["time"] = pd.to_datetime(
                df["time"],
                errors="coerce",
                format="mixed",
            )

        for col in required:
            if col not in df.columns:
                df[col] = 0 if col == "volume" else pd.NA

            df[col] = pd.to_numeric(df[col], errors="coerce")

        subset = ["open", "high", "low", "close"]
        if "time" in df.columns:
            subset = ["time", *subset]

        df = df.dropna(subset=subset).copy()

        if "time" in df.columns:
            df = df.sort_values("time").copy()

        df = df.reset_index(drop=True)

        return df

    def _strip_inline_comment(self, line: str) -> str:
        out = str(line or "")

        hash_index = out.find("#")
        slash_index = out.find("//")

        indexes = [idx for idx in [hash_index, slash_index] if idx >= 0]
        if not indexes:
            return out

        return out[: min(indexes)]

    def _normalize_strategy_aliases(self, line: str) -> str:
        out = str(line or "")

        replacements = {
            "ta.sma(": "sma(",
            "ta.ema(": "ema(",
            "ta.rsi(": "rsi(",
            "ta.highest(": "highest(",
            "ta.lowest(": "lowest(",
            "ta.atr(": "atr(",
            "ta.crossover(": "crossover(",
            "ta.crossunder(": "crossunder(",
        }

        for old, new in replacements.items():
            out = out.replace(old, new)

        return out

    def _normalize_function_name(self, name: str) -> str:
        name = str(name or "").strip().lower()
        return self.FUNCTION_ALIASES.get(name, name)

    def _handle_assignment(
        self,
        match: re.Match,
        bars: pd.DataFrame,
        series_context: dict[str, pd.Series],
        result: StrategyScriptResult,
        line: str,
    ) -> None:
        name = match.group("name").strip()
        func_name = self._normalize_function_name(match.group("func"))
        source_name = match.group("source").strip()
        length = int(match.group("length"))

        if func_name not in self.SUPPORTED_INDICATORS:
            result.errors.append(f"Unsupported function '{func_name}' in line: {line}")
            return

        source = series_context.get(source_name)

        if source is None:
            result.errors.append(f"Unknown source '{source_name}' in line: {line}")
            return

        try:
            output = self._calculate_indicator(
                func_name=func_name,
                source=source,
                length=length,
                bars=bars,
            )
            output = pd.Series(output, index=bars.index, name=name)
        except Exception as exc:
            result.errors.append(f"Error calculating '{name}': {exc}")
            return

        result.lines[name] = output
        series_context[name] = output

    def _calculate_indicator(
        self,
        func_name: str,
        source: pd.Series,
        length: int,
        bars: pd.DataFrame | None = None,
    ) -> pd.Series:
        source = pd.to_numeric(pd.Series(source), errors="coerce")
        length = max(1, int(length))

        if func_name == "sma":
            return source.rolling(length, min_periods=length).mean()

        if func_name == "ema":
            return source.ewm(span=length, adjust=False, min_periods=length).mean()

        if func_name == "rsi":
            delta = source.diff()
            gains = delta.clip(lower=0)
            losses = -delta.clip(upper=0)

            avg_gain = gains.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
            avg_loss = losses.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()

            rs = avg_gain / avg_loss.replace(0, pd.NA)
            rsi = 100 - (100 / (1 + rs))

            rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100)
            rsi = rsi.where(~((avg_loss == 0) & (avg_gain == 0)), 50)

            return rsi

        if func_name == "highest":
            return source.rolling(length, min_periods=length).max()

        if func_name == "lowest":
            return source.rolling(length, min_periods=length).min()

        if func_name == "atr":
            if bars is None:
                raise ValueError("ATR requires bars data.")

            high = pd.to_numeric(bars["high"], errors="coerce")
            low = pd.to_numeric(bars["low"], errors="coerce")
            close = pd.to_numeric(bars["close"], errors="coerce")
            prev_close = close.shift(1)

            tr1 = high - low
            tr2 = (high - prev_close).abs()
            tr3 = (low - prev_close).abs()
            true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

            return true_range.ewm(
                alpha=1 / length,
                adjust=False,
                min_periods=length,
            ).mean()

        raise ValueError(f"Unsupported indicator: {func_name}")

    def _handle_plot(self, match: re.Match, result: StrategyScriptResult) -> None:
        name = match.group("name").strip()

        if name not in result.lines:
            result.errors.append(f"Cannot plot unknown line: {name}")
            return

        if name not in result.plots:
            result.plots.append(name)

    def _build_backgrounds_from_rules(
        self,
        background_rules: list[tuple[str, str, str]],
        bars: pd.DataFrame,
        series_context: dict[str, pd.Series],
        conditions: dict[str, pd.Series],
        result: StrategyScriptResult,
    ) -> None:
        """
        Convert bgcolor commands into merged background ranges.

        Important for performance:
        - Do not draw one rectangle per candle.
        - Merge consecutive True candles into one range.
        - Cap total ranges before storing/rendering.
        """
        if not background_rules:
            return

        for expr, color, original_line in background_rules:
            condition = self._evaluate_condition_expression(
                expr=expr,
                bars=bars,
                series_context=series_context,
                conditions=conditions,
            )
            condition = self._to_bool_series(condition, bars)

            if condition is None:
                result.errors.append(f"Could not parse background condition: {original_line}")
                continue

            ranges = self._condition_to_background_ranges(
                condition=condition.reindex(bars.index).fillna(False).astype(bool),
                bars=bars,
                color=color,
                label=expr,
                rule=original_line,
            )

            if ranges:
                result.backgrounds.extend(ranges)

        if len(result.backgrounds) > self.MAX_RENDERED_BACKGROUNDS:
            result.backgrounds = result.backgrounds[-self.MAX_RENDERED_BACKGROUNDS:]

    def _condition_to_background_ranges(
        self,
        condition: pd.Series,
        bars: pd.DataFrame,
        color: str,
        label: str,
        rule: str,
    ) -> list[StrategyBackground]:
        ranges: list[StrategyBackground] = []

        if condition is None or condition.empty or bars is None or bars.empty:
            return ranges

        times = bars["time"] if "time" in bars.columns else pd.Series(bars.index, index=bars.index)

        start_idx = None
        end_idx = None

        for idx in bars.index:
            try:
                active = bool(condition.loc[idx])
            except Exception:
                active = False

            if active and start_idx is None:
                start_idx = int(idx)
                end_idx = int(idx)
                continue

            if active:
                end_idx = int(idx)
                continue

            if start_idx is not None and end_idx is not None:
                ranges.append(
                    self._make_background_range(
                        start_idx=start_idx,
                        end_idx=end_idx,
                        times=times,
                        color=color,
                        label=label,
                        rule=rule,
                    )
                )
                start_idx = None
                end_idx = None

        if start_idx is not None and end_idx is not None:
            ranges.append(
                self._make_background_range(
                    start_idx=start_idx,
                    end_idx=end_idx,
                    times=times,
                    color=color,
                    label=label,
                    rule=rule,
                )
            )

        return [bg for bg in ranges if bg is not None]

    def _make_background_range(
        self,
        start_idx: int,
        end_idx: int,
        times,
        color: str,
        label: str,
        rule: str,
    ) -> StrategyBackground | None:
        try:
            start_time = times.iloc[start_idx] if hasattr(times, "iloc") else times[start_idx]
            end_time = times.iloc[end_idx] if hasattr(times, "iloc") else times[end_idx]
        except Exception:
            return None

        return StrategyBackground(
            start_index=int(start_idx),
            end_index=int(end_idx),
            start_time=start_time,
            end_time=end_time,
            color=str(color or "blue"),
            label=str(label or ""),
            rule=str(rule or ""),
        )

    def _background_fill_color(self, color: str) -> str:
        key = str(color or "").strip().lower()

        palette = {
            "green": "rgba(34, 197, 94, 0.12)",
            "bull": "rgba(34, 197, 94, 0.12)",
            "bullish": "rgba(34, 197, 94, 0.12)",
            "red": "rgba(239, 68, 68, 0.12)",
            "bear": "rgba(239, 68, 68, 0.12)",
            "bearish": "rgba(239, 68, 68, 0.12)",
            "yellow": "rgba(234, 179, 8, 0.10)",
            "orange": "rgba(249, 115, 22, 0.10)",
            "blue": "rgba(59, 130, 246, 0.10)",
            "purple": "rgba(168, 85, 247, 0.10)",
            "gray": "rgba(148, 163, 184, 0.08)",
            "grey": "rgba(148, 163, 184, 0.08)",
        }

        if key in palette:
            return palette[key]

        # Allow a raw rgba(...) color for advanced users, but keep it simple.
        if key.startswith("rgba(") and key.endswith(")"):
            return str(color)

        if key.startswith("#") and len(key) in {4, 7}:
            return str(color)

        return palette["blue"]

    def _build_signals_from_rules(
        self,
        signal_rules: list[tuple[str, str, str]],
        bars: pd.DataFrame,
        series_context: dict[str, pd.Series],
        conditions: dict[str, pd.Series],
        result: StrategyScriptResult,
    ) -> None:
        """
        Evaluate all buy/sell rules once, then create a position-aware signal stream.

        This prevents scripts like:

            buy when close > fast
            sell when close < fast

        from producing a BUY marker on every candle while the condition remains true.
        Instead:
            - BUY fires only while flat.
            - SELL fires only while long.
        """

        if not signal_rules:
            return

        evaluated_rules: list[tuple[str, pd.Series, str]] = []

        for side, expr, original_line in signal_rules:
            condition = self._evaluate_condition_expression(
                expr=expr,
                bars=bars,
                series_context=series_context,
                conditions=conditions,
            )
            condition = self._to_bool_series(condition, bars)

            if condition is None:
                result.errors.append(f"Could not parse signal condition: {original_line}")
                continue

            evaluated_rules.append(
                (
                    str(side or "").upper().strip(),
                    condition.reindex(bars.index).fillna(False).astype(bool),
                    original_line,
                )
            )

        if not evaluated_rules:
            return

        close = pd.to_numeric(bars["close"], errors="coerce")
        times = bars["time"] if "time" in bars.columns else bars.index

        if not self.POSITION_AWARE_SIGNALS:
            for side, condition, original_line in evaluated_rules:
                for idx, is_signal in condition.items():
                    if not bool(is_signal):
                        continue

                    if idx >= len(close):
                        continue

                    price = close.iloc[idx]
                    if pd.isna(price):
                        continue

                    signal_time = times.iloc[idx] if hasattr(times, "iloc") else times[idx]

                    result.signals.append(
                        StrategySignal(
                            index=int(idx),
                            time=signal_time,
                            side=side,
                            price=float(price),
                            rule=original_line,
                        )
                    )

            result.signals.sort(key=lambda sig: sig.index)
            return

        in_position = False

        buy_rules = [
            (condition, original_line)
            for side, condition, original_line in evaluated_rules
            if side == "BUY"
        ]
        sell_rules = [
            (condition, original_line)
            for side, condition, original_line in evaluated_rules
            if side == "SELL"
        ]

        for idx in bars.index:
            if idx >= len(close):
                continue

            price = close.iloc[idx]
            if pd.isna(price):
                continue

            signal_time = times.iloc[idx] if hasattr(times, "iloc") else times[idx]

            if in_position:
                sell_rule = self._first_triggered_rule(idx, sell_rules)
                if sell_rule is not None:
                    result.signals.append(
                        StrategySignal(
                            index=int(idx),
                            time=signal_time,
                            side="SELL",
                            price=float(price),
                            rule=sell_rule,
                        )
                    )
                    in_position = False
                continue

            buy_rule = self._first_triggered_rule(idx, buy_rules)
            if buy_rule is not None:
                result.signals.append(
                    StrategySignal(
                        index=int(idx),
                        time=signal_time,
                        side="BUY",
                        price=float(price),
                        rule=buy_rule,
                    )
                )
                in_position = True

    def _first_triggered_rule(
        self,
        idx: int,
        rules: list[tuple[pd.Series, str]],
    ) -> str | None:
        for condition, original_line in rules:
            try:
                if bool(condition.loc[idx]):
                    return original_line
            except Exception:
                continue

        return None

    def _resolve_expression_value(
        self,
        name: str,
        bars: pd.DataFrame,
        series_context: dict[str, pd.Series],
        conditions: dict[str, pd.Series],
    ):
        name = str(name or "").strip()

        if name in conditions:
            return conditions[name]

        if name in series_context:
            return series_context[name]

        if name in bars.columns:
            return bars[name]

        try:
            number = float(name)
            return pd.Series(number, index=bars.index)
        except Exception:
            return None

    def _to_bool_series(self, value, bars: pd.DataFrame) -> pd.Series | None:
        if isinstance(value, pd.Series):
            return value.fillna(False).astype(bool)

        if isinstance(value, bool):
            return pd.Series(value, index=bars.index)

        if value is None:
            return None

        try:
            return pd.Series(bool(value), index=bars.index)
        except Exception:
            return None

    def _to_numeric_series(self, value, bars: pd.DataFrame) -> pd.Series | None:
        if isinstance(value, pd.Series):
            return pd.to_numeric(value, errors="coerce")

        if isinstance(value, (int, float)):
            return pd.Series(float(value), index=bars.index)

        if isinstance(value, bool):
            return pd.Series(float(value), index=bars.index)

        return None

    def _eval_ast_expression(
        self,
        node,
        bars: pd.DataFrame,
        series_context: dict[str, pd.Series],
        conditions: dict[str, pd.Series],
    ):
        if isinstance(node, ast.Expression):
            return self._eval_ast_expression(node.body, bars, series_context, conditions)

        if isinstance(node, ast.Name):
            return self._resolve_expression_value(node.id, bars, series_context, conditions)

        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, bool)):
                return pd.Series(node.value, index=bars.index)

            if isinstance(node.value, str):
                return node.value

            return None

        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            value = self._eval_ast_expression(node.operand, bars, series_context, conditions)
            value = self._to_bool_series(value, bars)
            if value is None:
                return None
            return ~value

        if isinstance(node, ast.BoolOp):
            values = [
                self._to_bool_series(
                    self._eval_ast_expression(child, bars, series_context, conditions),
                    bars,
                )
                for child in node.values
            ]

            if any(v is None for v in values):
                return None

            result = values[0]

            for value in values[1:]:
                if isinstance(node.op, ast.And):
                    result = result & value
                elif isinstance(node.op, ast.Or):
                    result = result | value
                else:
                    return None

            return result

        if isinstance(node, ast.Compare):
            left = self._eval_ast_expression(node.left, bars, series_context, conditions)
            left = self._to_numeric_series(left, bars)

            if left is None:
                return None

            result = pd.Series(True, index=bars.index)
            current_left = left

            for op, comparator in zip(node.ops, node.comparators):
                right = self._eval_ast_expression(comparator, bars, series_context, conditions)
                right = self._to_numeric_series(right, bars)

                if right is None:
                    return None

                if isinstance(op, ast.Gt):
                    part = current_left > right
                elif isinstance(op, ast.Lt):
                    part = current_left < right
                elif isinstance(op, ast.GtE):
                    part = current_left >= right
                elif isinstance(op, ast.LtE):
                    part = current_left <= right
                elif isinstance(op, ast.Eq):
                    part = current_left == right
                elif isinstance(op, ast.NotEq):
                    part = current_left != right
                else:
                    return None

                result = result & part.fillna(False)
                current_left = right

            return result

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                return None

            func_name = node.func.id.strip().lower()

            if func_name == "session":
                if len(node.args) != 1:
                    return None

                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    return self._make_session_condition(arg.value, bars)

                return None

            if func_name not in self.SUPPORTED_CONDITION_FUNCTIONS:
                return None

            if len(node.args) != 2:
                return None

            left = self._eval_ast_expression(node.args[0], bars, series_context, conditions)
            right = self._eval_ast_expression(node.args[1], bars, series_context, conditions)

            left = self._to_numeric_series(left, bars)
            right = self._to_numeric_series(right, bars)

            if left is None or right is None:
                return None

            if func_name == "crossover":
                return (left.shift(1) <= right.shift(1)) & (left > right)

            if func_name == "crossunder":
                return (left.shift(1) >= right.shift(1)) & (left < right)

        return None

    def _evaluate_condition_expression(
        self,
        expr: str,
        bars: pd.DataFrame,
        series_context: dict[str, pd.Series],
        conditions: dict[str, pd.Series],
    ) -> pd.Series | None:
        expr = str(expr or "").strip()
        expr = self._normalize_strategy_aliases(expr)

        if not expr:
            return None

        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            return None

        return self._eval_ast_expression(tree, bars, series_context, conditions)

    def _make_session_condition(self, session_text: str, bars: pd.DataFrame) -> pd.Series | None:
        """
        Build a boolean Series for a session window.

        Supports:
            session("0930-1600")
            session("09:30-16:00")
            session("0930-1130")
            session("1500-1600")
            overnight sessions like session("2000-0500")
        """

        if "time" not in bars.columns:
            return None

        text = str(session_text or "").strip().strip('"').strip("'")

        match = re.match(
            r"^(\d{1,2}):?(\d{2})\s*-\s*(\d{1,2}):?(\d{2})$",
            text,
        )

        if not match:
            return None

        start_hour = int(match.group(1))
        start_minute = int(match.group(2))
        end_hour = int(match.group(3))
        end_minute = int(match.group(4))

        if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23):
            return None

        if not (0 <= start_minute <= 59 and 0 <= end_minute <= 59):
            return None

        start_hhmm = start_hour * 100 + start_minute
        end_hhmm = end_hour * 100 + end_minute

        times = pd.to_datetime(bars["time"], errors="coerce")
        hhmm = times.dt.hour * 100 + times.dt.minute

        if start_hhmm <= end_hhmm:
            mask = (hhmm >= start_hhmm) & (hhmm <= end_hhmm)
        else:
            mask = (hhmm >= start_hhmm) | (hhmm <= end_hhmm)

        return pd.Series(mask.fillna(False).astype(bool), index=bars.index)

    def _find_blocked_token(self, line: str) -> str | None:
        lowered = str(line or "").lower()

        for word in sorted(self.BLOCKED_WORDS):
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])"
            if re.search(pattern, lowered):
                return word

        if "__" in lowered:
            return "__"

        return None
