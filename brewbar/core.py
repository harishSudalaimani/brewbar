"""
brewbar.core
~~~~~~~~~~~~

A progress bar library — common tqdm-style APIs plus extra features.

Public API:
    BrewBar       : main class
    bar           : alias of BrewBar()
    trange        : bar(range(*args))
    track         : decorator for auto-progress
    write         : print without clobbering active bars
    redirect_logging : route logging through bar.write
"""

from __future__ import annotations

import os
import sys
import time
import math
import asyncio
import shutil
import logging
import threading
import unicodedata
from collections import deque
from concurrent.futures import as_completed as _futures_as_completed
from contextlib import contextmanager
from typing import (
    AsyncIterable,
    AsyncIterator,
    Any,
    Callable,
    Deque,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    TextIO,
    Tuple,
    Union,
)

__all__ = [
    "BrewBar",
    "bar",
    "trange",
    "track",
    "track_futures",
    "track_tasks",
    "write",
    "redirect_logging",
    "BarGroup",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default Unicode block fills (8 sub-character steps for smooth rendering).
BLOCKS = " ▏▎▍▌▋▊▉█"
ASCII_FILL = "#"
ASCII_EMPTY = "-"

SPINNER_UNICODE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_ASCII = "|/-\\"

# Sparkline glyphs for rate-trend display.
SPARK = "▁▂▃▄▅▆▇█"

# 🍺 Brewing theme — the soul of brewbar.
# Beer glyph used to fill the bar in brew mode; empty cell is a faded mug.
BEER_FULL = "🍺"
BEER_EMPTY = "·"

# Brew stages — the bar walks through these as progress advances.
BREW_STAGES = (
    "mashing",
    "boiling",
    "fermenting",
    "conditioning",
    "cheers 🍻",
)

# Spinner frames for unknown-total brew mode.
BREW_SPINNER = ("🍺", "🍻", "🍺", "🍻")


COLORS = {
    "black": "\033[30m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    "bright_red": "\033[91m",
    "bright_green": "\033[92m",
    "bright_yellow": "\033[93m",
    "bright_blue": "\033[94m",
    "bright_cyan": "\033[96m",
}
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# ANSI cursor moves for multi-bar / position support.
CURSOR_UP = "\033[A"
CURSOR_DOWN = "\033[B"
ERASE_LINE = "\033[2K"
CARRIAGE_RETURN = "\r"

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_GLOBAL_LOCK = threading.RLock()
_ACTIVE_BARS: "List[BrewBar]" = []
_STREAM_STATES: Dict[int, Dict[str, Any]] = {}


def _get_stream_state(stream: TextIO) -> Dict[str, Any]:
    key = id(stream)
    state = _STREAM_STATES.get(key)
    if state is None or state["stream"] is not stream:
        state = {"stream": stream, "drawn_rows": 0, "static_lines": {}}
        _STREAM_STATES[key] = state
    return state


def _clear_drawn_rows(stream: TextIO, state: Dict[str, Any]) -> None:
    """Erase the previously drawn multi-line display and return to its top."""
    rows = state["drawn_rows"]
    if rows <= 0:
        return
    stream.write(CURSOR_UP * max(0, rows - 1))
    for row in range(rows):
        stream.write(CARRIAGE_RETURN + ERASE_LINE)
        if row < rows - 1:
            stream.write("\n")
    if rows > 1:
        stream.write(CURSOR_UP * (rows - 1))
    state["drawn_rows"] = 0


def _redraw_stream(
    stream: TextIO,
    override: Optional[Dict["BrewBar", str]] = None,
) -> None:
    """Redraw all visible bars on one TTY as a stable block of rows."""
    state = _get_stream_state(stream)
    _clear_drawn_rows(stream, state)

    lines = dict(state["static_lines"])
    override = override or {}
    bars = [
        b for b in _ACTIVE_BARS
        if b.file is stream and not b.disable and not b._closed and b._displayed
    ]
    for bar_obj in override:
        if bar_obj.file is stream and not bar_obj.disable:
            bars.append(bar_obj)
    bars = list(dict.fromkeys(bars))
    for bar_obj in sorted(bars, key=lambda b: b.position):
        line = override.get(bar_obj)
        if line is None:
            line, _ = bar_obj._format_output_line(time.monotonic(), final=False)
        lines[bar_obj.position] = line

    if lines:
        last_position = max(lines)
        for position in range(last_position + 1):
            stream.write(CARRIAGE_RETURN + ERASE_LINE + lines.get(position, ""))
            if position < last_position:
                stream.write("\n")
        state["drawn_rows"] = last_position + 1
    stream.flush()


# ---------------------------------------------------------------------------
# Optional dependencies — gracefully degrade
# ---------------------------------------------------------------------------

try:
    import psutil  # type: ignore
    _HAS_PSUTIL = True
    _PROCESS = psutil.Process(os.getpid())
except ImportError:
    _HAS_PSUTIL = False
    _PROCESS = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_interval(seconds: float, short: bool = False) -> str:
    """Format seconds as HH:MM:SS or compact like '1h2m'."""
    if seconds is None or seconds != seconds or seconds < 0 or math.isinf(seconds):
        return "?"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if short:
        if h:
            return f"{h}h{m:02}m"
        if m:
            return f"{m}m{s:02}s"
        return f"{s}s"
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _fmt_num(n: float, divisor: int = 1000) -> str:
    """Format a number with k/M/G/T suffixes (SI or binary depending on divisor)."""
    if n is None or (isinstance(n, float) and (n != n or math.isinf(n))):
        return "?"
    if divisor == 1024:
        units = ["", "Ki", "Mi", "Gi", "Ti", "Pi"]
    else:
        units = ["", "k", "M", "G", "T", "P"]
    n = float(n)
    for u in units:
        if abs(n) < divisor:
            if u == "":
                return f"{n:.0f}" if n == int(n) else f"{n:.2f}"
            return f"{n:.2f}{u}"
        n /= divisor
    return f"{n:.2f}E"


def _fmt_size(b: float) -> str:
    """Bytes → KiB/MiB/GiB."""
    return _fmt_num(b, divisor=1024) + "B"


def _fmt_rate(rate: float, unit: str = "it", unit_scale: bool = False, divisor: int = 1000) -> str:
    """Format a rate. Falls back to s/it when rate < 1."""
    if rate is None or rate <= 0 or math.isinf(rate) or rate != rate:
        return f"?{unit}/s"
    if rate >= 1:
        val = _fmt_num(rate, divisor) if unit_scale else f"{rate:.2f}"
        return f"{val}{unit}/s"
    inv = 1.0 / rate
    return f"{inv:.2f}s/{unit}"


def _visible_len(text: str) -> int:
    """Return terminal columns used by text, ignoring ANSI control codes."""
    out = 0
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\033":
            # Skip CSI and other short ANSI control sequences.
            while i < len(text) and text[i] not in "@ABCDEFGHIJKLMNOPQRSTUVWXYZ`abcdefghijklmnopqrstuvwxyz~":
                i += 1
            i += 1
        else:
            if unicodedata.combining(c) or unicodedata.category(c) in ("Mn", "Me", "Cf"):
                i += 1
                continue
            out += 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
            i += 1
    return out


def _truncate_display(text: str, width: int) -> str:
    """Truncate text at a terminal-column boundary, preserving ANSI codes."""
    if width <= 0:
        return ""
    out = []
    used = 0
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\033":
            start = i
            while i < len(text) and text[i] not in "@ABCDEFGHIJKLMNOPQRSTUVWXYZ`abcdefghijklmnopqrstuvwxyz~":
                i += 1
            i += 1
            out.append(text[start:i])
            continue
        if unicodedata.combining(c) or unicodedata.category(c) in ("Mn", "Me", "Cf"):
            out.append(c)
            i += 1
            continue
        char_width = 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
        if used + char_width > width:
            break
        out.append(c)
        used += char_width
        i += 1
    return "".join(out)


def _strip_ansi(text: str) -> str:
    out = []
    i = 0
    while i < len(text):
        if text[i] == "\033":
            while i < len(text) and text[i] not in "@ABCDEFGHIJKLMNOPQRSTUVWXYZ`abcdefghijklmnopqrstuvwxyz~":
                i += 1
            i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _supports_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if sys.platform == "win32":
        return (
            "WT_SESSION" in os.environ
            or "ANSICON" in os.environ
            or os.environ.get("TERM") == "xterm-256color"
        )
    return True


def _enable_windows_ansi() -> None:
    """Best-effort enable of ANSI on legacy Windows consoles."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


_enable_windows_ansi()


# ---------------------------------------------------------------------------
# BrewBar
# ---------------------------------------------------------------------------

class BrewBar:
    """
    A serious, full-featured progress bar.

    Supports common tqdm-style usage and adds async, monitoring, and event
    features. See the README for compatibility details.
    """

    # tqdm-compatible class-level lock (use BrewBar.get_lock() in multiprocess).
    _instance_lock = threading.RLock()

    # ----- construction -------------------------------------------------

    def __init__(
        self,
        iterable: Optional[Union[Iterable, AsyncIterable]] = None,
        *,
        # --- tqdm-compatible ---
        desc: Optional[str] = None,
        total: Optional[Union[int, float]] = None,
        leave: bool = True,
        file: Optional[TextIO] = None,
        ncols: Optional[int] = None,
        mininterval: float = 0.1,
        maxinterval: float = 10.0,
        miniters: Optional[int] = None,
        ascii: Union[bool, str, None] = None,
        disable: Optional[bool] = None,
        unit: str = "it",
        unit_scale: Union[bool, int, float] = False,
        unit_divisor: int = 1000,
        dynamic_ncols: bool = True,
        smoothing: float = 0.3,
        bar_format: Optional[str] = None,
        initial: int = 0,
        position: Optional[int] = None,
        postfix: Union[str, dict, None] = None,
        delay: float = 0.0,
        # --- BrewBar extras ---
        colour: Optional[str] = None,  # tqdm spelling, accepted
        color: Optional[Union[bool, str]] = None,
        auto_color: bool = False,
        show_memory: bool = False,
        show_cpu: bool = False,
        show_sparkline: bool = False,
        sparkline_width: int = 8,
        eta_confidence: bool = False,
        eta_budget: Optional[float] = None,
        on_update: Optional[Callable[["BrewBar"], None]] = None,
        on_complete: Optional[Callable[["BrewBar"], None]] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_interval: Optional[Tuple[float, Callable[["BrewBar"], None]]] = None,
        track_metrics: Optional[List[str]] = None,
        write_safe: bool = True,
        # --- 🍺 brewing theme ---
        brew: bool = True,             # use 🍺 fill glyph + brew stage labels
        show_stage: Optional[bool] = None,  # show "mashing/boiling/..." label; defaults to brew
    ):
        # --- iterable / total ---
        self.iterable = iterable
        if total is None:
            try:
                total = len(iterable)  # type: ignore[arg-type]
            except (TypeError, AttributeError):
                total = None
        self.total = total

        # --- display config ---
        self.desc = desc or ""
        self.leave = leave
        self.file = file if file is not None else sys.stderr
        self.ncols = ncols
        self.dynamic_ncols = dynamic_ncols
        self.mininterval = max(0.0, mininterval)
        self.maxinterval = maxinterval
        self.miniters_user = miniters
        self.miniters = miniters if miniters is not None else 1
        self._dynamic_miniters = miniters is None
        self.smoothing = max(0.0, min(1.0, smoothing))
        self.bar_format = bar_format
        self.delay = max(0.0, delay)
        self.write_safe = write_safe

        # --- ascii / glyphs ---
        if ascii is None:
            self.ascii_chars = None  # use unicode blocks
        elif ascii is True:
            self.ascii_chars = " #"
        elif isinstance(ascii, str):
            # tqdm allows custom char set, e.g. " 123456789#"
            self.ascii_chars = ascii
        else:
            self.ascii_chars = None

        # --- units ---
        self.unit = unit
        self.unit_scale = unit_scale
        self.unit_divisor = unit_divisor

        # --- color ---
        # Accept both `color` (BrewBar) and `colour` (tqdm) spelling.
        chosen_color = colour if colour is not None else color
        if chosen_color is True:
            self.color: Optional[str] = "cyan"
        elif isinstance(chosen_color, str):
            self.color = chosen_color
        else:
            self.color = None
        self.auto_color = auto_color
        if self.color and not _supports_color(self.file):
            # Keep value (might be needed later), but don't emit.
            self._color_active = False
        else:
            self._color_active = bool(self.color or self.auto_color)

        # --- extras ---
        self.show_memory = show_memory and _HAS_PSUTIL
        self.show_cpu = show_cpu and _HAS_PSUTIL
        self.show_sparkline = show_sparkline
        self.sparkline_width = max(3, sparkline_width)
        self.eta_confidence = eta_confidence
        self.eta_budget = eta_budget
        self.on_update_cb = on_update
        self.on_complete_cb = on_complete
        self.on_event_cb = on_event
        self.on_interval_cb = on_interval
        self._last_interval_call = 0.0
        self.track_metrics = list(track_metrics) if track_metrics else []
        self._metric_history: Dict[str, Dict[str, float]] = {}

        # --- 🍺 brew theme ---
        # If user passed `ascii`, force off the brew glyphs (CI / log-safe).
        # If user passed an explicit `bar_format` with their own {bar}, we
        # still use brew glyphs inside it (they own layout, we own glyph).
        self.brew = bool(brew) and self.ascii_chars is None
        self.show_stage = self.brew if show_stage is None else bool(show_stage)

        # --- disable detection ---
        if disable is None:
            env_disable = os.environ.get("BREWBAR_DISABLE")
            if env_disable is not None:
                self.disable = env_disable.strip().lower() in ("1", "true", "yes", "on")
            else:
                self.disable = not (hasattr(self.file, "isatty") and self.file.isatty())
        else:
            self.disable = bool(disable)

        # --- postfix ---
        self._postfix_str = ""
        if postfix is not None:
            self.set_postfix(postfix, refresh=False)

        # --- counters / timing ---
        self.initial = initial
        self.n = initial
        self.last_print_n = initial
        self.start_time: Optional[float] = None
        self.last_print_t: float = 0.0
        self._paused = False
        self._paused_time = 0.0
        self._pause_start: Optional[float] = None

        # --- rate smoothing & history ---
        self._avg_rate: float = 0.0
        self._rate_history: Deque[float] = deque(maxlen=self.sparkline_width)
        # For predictive ETA: store (t, n) pairs for linear regression.
        self._sample_history: Deque[Tuple[float, int]] = deque(maxlen=20)

        # --- render state ---
        self._last_len: int = 0
        self._last_render_n = self.initial
        self._last_rendered_line: Optional[str] = None
        self._closed: bool = False
        self._spinner_index: int = 0
        self._lock = threading.RLock()
        self._displayed = False  # tracks whether we've drawn anything yet

        # --- position & registry ---
        with _GLOBAL_LOCK:
            if position is None:
                occupied = [
                    b.position for b in _ACTIVE_BARS
                    if b.file is self.file and not b._closed and not b.disable
                ]
                state = _STREAM_STATES.get(id(self.file))
                if state is not None and state["stream"] is self.file:
                    occupied.extend(state["static_lines"])
                self.position = max(occupied, default=-1) + 1
            else:
                self.position = position
            _ACTIVE_BARS.append(self)

    # ----- class methods (tqdm compatibility) -------------------------

    @classmethod
    def get_lock(cls) -> threading.RLock:
        return cls._instance_lock

    @classmethod
    def set_lock(cls, lock: threading.RLock) -> None:
        cls._instance_lock = lock

    @classmethod
    def write(cls, msg: str, file: Optional[TextIO] = None, end: str = "\n", nolock: bool = False) -> None:
        """Print without clobbering active bars (class method form)."""
        write(msg, file=file, end=end, nolock=nolock)

    # ----- context manager ---------------------------------------------

    def __enter__(self) -> "BrewBar":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    async def __aenter__(self) -> "BrewBar":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # ----- iterator protocol -------------------------------------------

    def __iter__(self) -> Iterator:
        if self.iterable is None:
            raise TypeError("BrewBar.__iter__() called without an iterable")

        if self.disable:
            try:
                if self.on_event_cb is None:
                    yield from self.iterable
                else:
                    for obj in self.iterable:
                        yield obj
                        self.update(1)
            finally:
                self.close()
            return

        if self.total == 0:
            self.close()
            return

        self._init_timer()
        try:
            for obj in self.iterable:
                yield obj
                self.update(1)
        finally:
            self.close()

    def __aiter__(self) -> AsyncIterator[Any]:
        """Iterate over an async iterable while tracking each yielded item."""
        return self._async_iter()

    async def _async_iter(self) -> AsyncIterator[Any]:
        source = self.iterable
        if source is None or not hasattr(source, "__aiter__"):
            raise TypeError("BrewBar.__aiter__() requires an async iterable")

        if self.disable:
            try:
                if self.on_event_cb is None:
                    async for obj in source:  # type: ignore[attr-defined]
                        yield obj
                else:
                    async for obj in source:  # type: ignore[attr-defined]
                        yield obj
                        self.update(1)
            finally:
                self.close()
            return

        if self.total == 0:
            self.close()
            return

        self._init_timer()
        try:
            async for obj in source:  # type: ignore[attr-defined]
                yield obj
                self.update(1)
        finally:
            self.close()

    def __len__(self) -> int:
        if self.total is not None:
            return int(self.total)
        try:
            return len(self.iterable)  # type: ignore[arg-type]
        except (TypeError, AttributeError):
            raise TypeError("BrewBar has no length")

    # ----- timing / state ---------------------------------------------

    def _init_timer(self) -> None:
        if self.start_time is None:
            self.start_time = time.monotonic()
            self.last_print_t = self.start_time

    def _elapsed(self) -> float:
        if self.start_time is None:
            return 0.0
        now = time.monotonic()
        # Subtract paused time. If currently paused, also subtract live pause.
        live_pause = (now - self._pause_start) if self._paused and self._pause_start else 0.0
        return now - self.start_time - self._paused_time - live_pause

    # ----- public API ---------------------------------------------------

    def update(self, n: Union[int, float] = 1) -> bool:
        """
        Advance counter by `n`. Returns True if a render happened.
        Matches tqdm's update() semantics.
        """
        do_render = False
        interval_callback = None
        paused = False
        delayed = False
        event_only = False
        now = time.monotonic()
        with self._lock:
            if self._closed:
                return False
            if self.disable:
                if self.on_event_cb is None:
                    return False
                self._init_timer()
                self.n += n
                event_only = True
            elif self._paused:
                # Counters advance, but visual updates are suppressed.
                self.n += n
                paused = True
            else:
                self._init_timer()
                self.n += n

                now = time.monotonic()
                # Defer first render if user asked to delay.
                delayed = self.delay > 0 and (now - self.start_time) < self.delay  # type: ignore[operator]
                if not delayed:
                    delta_n = self.n - self.last_print_n
                    delta_t = now - self.last_print_t

                    # Determine whether to render. Mirrors tqdm logic.
                    do_render = (
                        (delta_n >= self.miniters and delta_t >= self.mininterval)
                        or delta_t >= self.maxinterval
                    )

                    if do_render:
                        self._update_rate(now)
                        self._auto_tune_miniters(delta_n, delta_t)
                        self.last_print_t = now
                        self.last_print_n = self.n

                    if self.on_interval_cb:
                        interval, callback = self.on_interval_cb
                        if now - self._last_interval_call >= interval:
                            self._last_interval_call = now
                            interval_callback = callback

        if event_only:
            self._emit_event("update")
            return False

        if not paused and not delayed and do_render:
            with _GLOBAL_LOCK:
                if not self._closed:
                    self._render(now=now)
            if self.on_update_cb:
                try:
                    self.on_update_cb(self)
                except Exception:
                    pass

        # Interval callback fires independently of the render throttle.
        if not paused and not delayed and interval_callback:
            try:
                interval_callback(self)
            except Exception:
                pass

        self._emit_event("update")

        return do_render

    def _auto_tune_miniters(self, delta_n: float, delta_t: float) -> None:
        """tqdm-style dynamic miniters adjustment."""
        if not self._dynamic_miniters or delta_t <= 0:
            return
        # Target: enough items to hit mininterval comfortably.
        target = max(1, int(delta_n * (self.mininterval / delta_t)))
        # Smooth the change to avoid oscillation.
        self.miniters = max(1, int(0.7 * self.miniters + 0.3 * target))

    def refresh(self, nolock: bool = False) -> None:
        """Force a re-render, ignoring throttling."""
        if self.disable or self._closed:
            return
        self._init_timer()
        now = time.monotonic()
        self._update_rate(now)
        if nolock:
            self._render(now=now, force=True)
        else:
            with _GLOBAL_LOCK:
                self._render(now=now, force=True)

    def display(self, msg: Optional[str] = None, pos: Optional[int] = None) -> None:
        """Alias to refresh() for tqdm compatibility."""
        if msg is not None:
            self.set_description(msg, refresh=False)
        if pos is not None:
            self.position = pos
        self.refresh()

    def clear(self, nolock: bool = False) -> None:
        """Erase the current rendering without closing."""
        if self.disable:
            return
        if not hasattr(self.file, "isatty") or not self.file.isatty():
            return
        ctx = _noop_cm() if nolock else _GLOBAL_LOCK
        with ctx:
            self._displayed = False
            self._last_rendered_line = None
            _redraw_stream(self.file)

    def reset(self, total: Optional[int] = None) -> None:
        """Reset counters; optionally change total."""
        with self._lock:
            self.n = self.initial
            self.last_print_n = self.initial
            self.start_time = None
            self.last_print_t = 0.0
            self._avg_rate = 0.0
            self._rate_history.clear()
            self._sample_history.clear()
            self._spinner_index = 0
            self._paused = False
            self._paused_time = 0.0
            self._pause_start = None
            self._metric_history.clear()
            if total is not None:
                self.total = total

    @property
    def format_dict(self) -> Dict[str, Any]:
        """Current values available to custom formatters (tqdm-compatible)."""
        return self._build_format_dict(time.monotonic(), final=False)

    def snapshot(self) -> Dict[str, Any]:
        """Return a small JSON-friendly progress snapshot."""
        with self._lock:
            elapsed = self._elapsed()
            eta, _ = self._predict_eta()
            percentage = None
            if self.total is not None and self.total > 0:
                percentage = min(100.0, 100.0 * self.n / self.total)
            return {
                "description": self.desc,
                "n": self.n,
                "total": self.total,
                "percentage": percentage,
                "elapsed": elapsed,
                "rate": self._avg_rate,
                "eta": eta if math.isfinite(eta) else None,
                "unit": self.unit,
            }

    def _emit_event(self, event: str) -> None:
        if self.on_event_cb is None:
            return
        payload = {"event": event, **self.snapshot()}
        try:
            self.on_event_cb(payload)
        except Exception:
            pass

    def set_description(self, desc: Optional[str] = None, refresh: bool = True) -> None:
        self.desc = desc or ""
        if refresh:
            self.refresh()

    def set_description_str(self, desc: Optional[str] = None, refresh: bool = True) -> None:
        """tqdm compatibility — same as set_description for us."""
        self.set_description(desc, refresh=refresh)

    def set_postfix(
        self,
        postfix: Union[str, dict, None] = None,
        refresh: bool = True,
        **kwargs: Any,
    ) -> None:
        if kwargs:
            postfix = kwargs if postfix is None else {**(postfix or {}), **kwargs}

        if postfix is None:
            self._postfix_str = ""
        elif isinstance(postfix, dict):
            parts = []
            for k, v in postfix.items():
                if isinstance(v, float):
                    parts.append(f"{k}={v:.4g}")
                else:
                    parts.append(f"{k}={v}")
                # Track metrics for min/max/avg
                if k in self.track_metrics and isinstance(v, (int, float)):
                    h = self._metric_history.setdefault(
                        k, {"min": float("inf"), "max": float("-inf"), "sum": 0.0, "count": 0, "last": 0.0}
                    )
                    h["min"] = min(h["min"], v)
                    h["max"] = max(h["max"], v)
                    h["sum"] += v
                    h["count"] += 1
                    h["last"] = v
            self._postfix_str = ", ".join(parts)
        else:
            self._postfix_str = str(postfix)

        if refresh:
            self.refresh()

    def set_postfix_str(self, s: str = "", refresh: bool = True) -> None:
        self._postfix_str = str(s)
        if refresh:
            self.refresh()

    def pause(self) -> None:
        """Pause the timer (e.g., during a blocking I/O wait)."""
        with self._lock:
            if not self._paused:
                self._paused = True
                self._pause_start = time.monotonic()

    def resume(self) -> None:
        """Resume after pause()."""
        with self._lock:
            if self._paused and self._pause_start is not None:
                self._paused_time += time.monotonic() - self._pause_start
                self._pause_start = None
                self._paused = False

    @contextmanager
    def paused(self) -> Iterator[None]:
        """Context manager: time inside is not counted."""
        self.pause()
        try:
            yield
        finally:
            self.resume()

    def metric_summary(self) -> Dict[str, Dict[str, float]]:
        """Return tracked metric summaries (min/max/avg/last)."""
        out = {}
        for k, h in self._metric_history.items():
            if h["count"] > 0:
                out[k] = {
                    "min": h["min"],
                    "max": h["max"],
                    "avg": h["sum"] / h["count"],
                    "last": h["last"],
                    "count": int(h["count"]),
                }
        return out

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True

        if not self.disable and hasattr(self.file, "isatty") and self.file.isatty():
            with _GLOBAL_LOCK:
                if self._displayed or self.n > self.initial:
                    self._render(final=True, force=True)
                state = _get_stream_state(self.file)
                if self.leave and self._displayed and self._last_rendered_line is not None:
                    state["static_lines"][self.position] = self._last_rendered_line

                try:
                    _ACTIVE_BARS.remove(self)
                except ValueError:
                    pass

                if not self.leave:
                    state["static_lines"].pop(self.position, None)
                    for other in _ACTIVE_BARS:
                        if other.file is self.file and other.position > self.position:
                            other.position -= 1
                    state["static_lines"] = {
                        (pos - 1 if pos > self.position else pos): line
                        for pos, line in state["static_lines"].items()
                    }

                _redraw_stream(self.file)
                remaining = [b for b in _ACTIVE_BARS if b.file is self.file and not b.disable]
                if not remaining:
                    if state["static_lines"]:
                        self.file.write("\n")
                    state["drawn_rows"] = 0
                    state["static_lines"].clear()
                    _STREAM_STATES.pop(id(self.file), None)
                self.file.flush()
        else:
            needs_final = (self._displayed and self.n != self._last_render_n) or (
                not self._displayed and self.n > self.initial
            )
            if not self.disable and needs_final:
                self._render(final=True, force=True)
            with _GLOBAL_LOCK:
                try:
                    _ACTIVE_BARS.remove(self)
                except ValueError:
                    pass

        self._emit_event("complete")

        if self.on_complete_cb:
            try:
                self.on_complete_cb(self)
            except Exception:
                pass

    # ----- rate / ETA computation --------------------------------------

    def _update_rate(self, now: float) -> None:
        if self.start_time is None:
            return
        elapsed = self._elapsed()
        if elapsed <= 0:
            return

        dn = self.n - self.last_print_n
        dt = now - self.last_print_t

        if dt > 0 and dn > 0:
            inst = dn / dt
        else:
            inst = (self.n - self.initial) / elapsed if elapsed > 0 else 0.0

        # EMA smoothing.
        if self._avg_rate == 0:
            self._avg_rate = inst
        elif self.smoothing == 0:
            self._avg_rate = (self.n - self.initial) / elapsed
        else:
            self._avg_rate = (
                self.smoothing * inst + (1 - self.smoothing) * self._avg_rate
            )

        self._rate_history.append(inst)
        self._sample_history.append((now, self.n))

    def _predict_eta(self) -> Tuple[float, float]:
        """
        Predictive ETA using linear regression on (time, count) history.
        Returns (eta_seconds, confidence_window_seconds).
        Falls back to average rate when not enough samples.
        """
        if self.total is None or self.total <= 0 or self.n >= self.total:
            return 0.0, 0.0
        remaining = self.total - self.n

        if len(self._sample_history) < 3:
            if self._avg_rate > 0:
                return remaining / self._avg_rate, 0.0
            return float("inf"), 0.0

        # Simple linear regression: n = a + b*t
        ts = [t for t, _ in self._sample_history]
        ns = [n for _, n in self._sample_history]
        t0 = ts[0]
        xs = [t - t0 for t in ts]
        n_pts = len(xs)
        mean_x = sum(xs) / n_pts
        mean_y = sum(ns) / n_pts
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ns))
        den = sum((x - mean_x) ** 2 for x in xs)
        if den <= 0:
            slope = self._avg_rate
        else:
            slope = num / den
        if slope <= 0:
            return float("inf"), 0.0

        eta = remaining / slope

        # Confidence window: std deviation of recent rates, scaled to time.
        rates = list(self._rate_history)
        if len(rates) >= 3:
            mean_r = sum(rates) / len(rates)
            var = sum((r - mean_r) ** 2 for r in rates) / len(rates)
            stddev = math.sqrt(var)
            # Convert rate stddev to ETA window
            if mean_r > 0:
                conf = min(eta, (stddev / mean_r) * eta)
            else:
                conf = 0.0
        else:
            conf = 0.0
        return eta, conf

    # ----- rendering ---------------------------------------------------

    def _term_width(self) -> int:
        if self.ncols is not None:
            return max(10, self.ncols)
        if not self.dynamic_ncols:
            return 80
        try:
            return shutil.get_terminal_size((80, 20)).columns
        except OSError:
            return 80

    def _render_bar(self, percent: float, width: int) -> str:
        if width <= 0:
            return ""

        # 🍺 brew mode: each cell is one emoji wide visually but ~1 char in
        # our render budget. Use chunky beer glyphs, no sub-cell precision.
        if self.brew:
            filled = int(round(percent * width))
            filled = max(0, min(width, filled))
            empty = width - filled
            return BEER_FULL * filled + BEER_EMPTY * empty

        if self.ascii_chars is not None:
            charset = self.ascii_chars
        else:
            charset = BLOCKS
        n_chars = len(charset) - 1  # last char is "full"
        if n_chars <= 0:
            return ""
        # Sub-character precision for smooth rendering.
        full_units = percent * width * n_chars
        full = int(full_units // n_chars)
        remainder = int(full_units - full * n_chars)
        full = min(full, width)
        empty = width - full
        bar = charset[-1] * full
        if empty > 0 and remainder > 0:
            bar += charset[remainder]
            empty -= 1
        bar += charset[0] * empty
        return bar

    def _render_sparkline(self) -> str:
        if not self._rate_history:
            return SPARK[0] * self.sparkline_width
        rates = list(self._rate_history)
        # Pad with leading zeros if not full yet.
        while len(rates) < self.sparkline_width:
            rates.insert(0, 0.0)
        rates = rates[-self.sparkline_width :]
        mx = max(rates) if max(rates) > 0 else 1.0
        out = []
        for r in rates:
            idx = int((r / mx) * (len(SPARK) - 1))
            idx = max(0, min(len(SPARK) - 1, idx))
            out.append(SPARK[idx])
        return "".join(out)

    def _pick_auto_color(self, eta: float, elapsed: float) -> Optional[str]:
        """Heuristic for auto_color: green/yellow/red based on pace."""
        if not self.auto_color:
            return None
        if self.total is None:
            return None
        # If budget set, color by budget burn rate.
        if self.eta_budget is not None and self.eta_budget > 0:
            projected_total = elapsed + eta
            ratio = projected_total / self.eta_budget
            if ratio < 0.9:
                return "bright_green"
            if ratio < 1.1:
                return "bright_yellow"
            return "bright_red"
        # Otherwise, look at rate trend.
        if len(self._rate_history) >= 4:
            half = len(self._rate_history) // 2
            recent = list(self._rate_history)[-half:]
            older = list(self._rate_history)[:-half]
            if older and recent:
                r_old = sum(older) / len(older)
                r_new = sum(recent) / len(recent)
                if r_old > 0:
                    if r_new >= r_old * 0.95:
                        return "bright_green"
                    if r_new >= r_old * 0.7:
                        return "bright_yellow"
                    return "bright_red"
        return "cyan"

    def _build_format_dict(self, now: float, final: bool) -> Dict[str, Any]:
        elapsed = self._elapsed()
        rate = self._avg_rate

        if self.unit_scale:
            divisor = self.unit_divisor
            n_fmt = _fmt_num(self.n, divisor)
            total_fmt = _fmt_num(self.total, divisor) if self.total is not None else "?"
        else:
            n_fmt = str(int(self.n))
            total_fmt = str(int(self.total)) if self.total is not None else "?"

        rate_fmt = _fmt_rate(rate, self.unit, self.unit_scale, self.unit_divisor) if rate > 0 else f"?{self.unit}/s"

        if self.total and self.total > 0:
            percentage = min(100.0, 100.0 * self.n / self.total)
        else:
            percentage = 0.0

        eta, eta_conf = self._predict_eta()
        remaining_fmt = _fmt_interval(eta) if eta != float("inf") else "?"
        elapsed_fmt = _fmt_interval(elapsed)

        # Memory / CPU
        mem_fmt = ""
        cpu_fmt = ""
        if self.show_memory and _PROCESS is not None:
            try:
                rss = _PROCESS.memory_info().rss
                mem_fmt = _fmt_size(rss)
            except Exception:
                mem_fmt = "?"
        if self.show_cpu and _PROCESS is not None:
            try:
                cpu_fmt = f"{_PROCESS.cpu_percent(interval=None):.0f}%"
            except Exception:
                cpu_fmt = "?"

        spark = self._render_sparkline() if self.show_sparkline else ""

        # ETA with confidence
        if self.eta_confidence and eta != float("inf") and eta_conf > 0:
            remaining_conf_fmt = f"{_fmt_interval(eta)}±{_fmt_interval(eta_conf)}"
        else:
            remaining_conf_fmt = remaining_fmt

        # 🍺 Brew stage label (mashing → boiling → ... → cheers 🍻)
        if self.total and self.total > 0:
            stage_idx = min(
                int((self.n / self.total) * len(BREW_STAGES)),
                len(BREW_STAGES) - 1,
            )
            stage = BREW_STAGES[stage_idx]
        else:
            stage = "brewing"

        return {
            "n": int(self.n),
            "n_fmt": n_fmt,
            "total": self.total,
            "total_fmt": total_fmt,
            "percentage": percentage,
            "rate": rate,
            "rate_fmt": rate_fmt,
            "rate_noinv_fmt": rate_fmt,
            "elapsed": elapsed,
            "elapsed_s": elapsed,
            "elapsed_fmt": elapsed_fmt,
            "remaining": eta if eta != float("inf") else 0,
            "remaining_s": eta if eta != float("inf") else 0,
            "remaining_fmt": remaining_fmt,
            "remaining_conf_fmt": remaining_conf_fmt,
            "desc": self.desc,
            "postfix": self._postfix_str,
            "unit": self.unit,
            "memory": mem_fmt,
            "cpu": cpu_fmt,
            "sparkline": spark,
            "stage": stage,
            "bar": "",  # filled below once width is known
        }

    def _build_line(self, now: float, final: bool) -> str:
        fd = self._build_format_dict(now, final)

        # Spinner mode: unknown total.
        if self.total is None:
            if self.brew:
                # 🍺 brew spinner alternates beer mugs
                frame = "🍻" if final else BREW_SPINNER[self._spinner_index % len(BREW_SPINNER)]
            else:
                frames = SPINNER_ASCII if self.ascii_chars is not None else SPINNER_UNICODE
                frame = "✓" if (final and self.ascii_chars is None) else (
                    "+" if final else frames[self._spinner_index % len(frames)]
                )
            if not final:
                self._spinner_index += 1

            head = f"{frame} brewing..." if self.brew else f"{frame} {fd['n_fmt']}{self.unit}"
            parts = [head]
            if self.brew:
                parts.append(f"{fd['n_fmt']}{self.unit}")
            parts.append(fd["elapsed_fmt"])
            parts.append(fd["rate_fmt"])
            if self.show_memory:
                parts.append(f"mem {fd['memory']}")
            if self.show_cpu:
                parts.append(f"cpu {fd['cpu']}")
            if fd["postfix"]:
                parts.append(fd["postfix"])
            line = " | ".join(parts)
            if self.desc:
                line = f"{self.desc}: {line}"
            return line

        # Known total. Build prefix/suffix; bar fills remaining width.
        prefix_parts = []
        if self.desc:
            prefix_parts.append(f"{self.desc}:")
        prefix_parts.append(f"{fd['percentage']:3.0f}%")
        prefix = " ".join(prefix_parts)

        suffix_parts = [f"{fd['n_fmt']}/{fd['total_fmt']}"]
        suffix_parts.append(f"[{fd['elapsed_fmt']}<{fd['remaining_conf_fmt']}, {fd['rate_fmt']}]")
        # 🍺 brew stage label
        if self.show_stage:
            suffix_parts.append(fd["stage"])
        if self.show_memory:
            suffix_parts.append(f"mem={fd['memory']}")
        if self.show_cpu:
            suffix_parts.append(f"cpu={fd['cpu']}")
        if self.show_sparkline:
            suffix_parts.append(fd["sparkline"])
        if fd["postfix"]:
            suffix_parts.append(fd["postfix"])
        suffix = " ".join(suffix_parts)

        # Custom format string takes precedence.
        if self.bar_format:
            # User specifies layout; compute bar with their width hint (or 10).
            bar_width = self.ncols or 10
            fd["bar"] = self._render_bar(fd["percentage"] / 100, bar_width)
            try:
                return self.bar_format.format(**fd, l_bar="", r_bar="")
            except (KeyError, IndexError):
                # Fallback to default rendering if format string is malformed.
                pass

        # Fit bar in remaining space. Position selects a terminal row.
        term_w = self._term_width()
        used = _visible_len(prefix) + _visible_len(suffix) + 4  # spaces around bar
        col_per_cell = 2 if self.brew else 1
        bar_width = max(3, (term_w - used) // col_per_cell)
        # Cap bar width sensibly so we don't get an absurdly long bar
        bar_width = min(bar_width, 30 if self.brew else 60)
        bar_str = self._render_bar(fd["percentage"] / 100, bar_width)
        return f"{prefix} |{bar_str}| {suffix}"

    def _format_output_line(self, now: float, final: bool = False) -> Tuple[str, int]:
        with self._lock:
            line = self._build_line(now, final)
            visible_len = _visible_len(line)
            term_w = self._term_width()
            if visible_len > term_w:
                line = _truncate_display(line, term_w - 1)
                visible_len = term_w - 1

            color = None
            is_tty = hasattr(self.file, "isatty") and self.file.isatty()
            if is_tty and self.auto_color:
                eta, _ = self._predict_eta()
                color = self._pick_auto_color(eta if eta != float("inf") else 0.0, self._elapsed())
            elif is_tty and self.color and self._color_active:
                color = self.color

            styled = f"{COLORS[color]}{line}{RESET}" if color in COLORS else line
            return styled, visible_len

    def _render(self, now: Optional[float] = None, final: bool = False, force: bool = False) -> None:
        if self.disable:
            return
        if now is None:
            now = time.monotonic()
        self._init_timer()

        if hasattr(self.file, "isatty") and self.file.isatty():
            with _GLOBAL_LOCK:
                with self._lock:
                    styled, visible_len = self._format_output_line(now, final)
                    self._last_rendered_line = styled
                    self._last_render_n = self.n
                    self._last_len = visible_len
                    self._displayed = True
                    _redraw_stream(self.file, override={self: styled})
            return

        with self._lock:
            styled, visible_len = self._format_output_line(now, final)
            self._last_rendered_line = styled
            self._last_render_n = self.n
            self._last_len = visible_len
            self._displayed = True
            # Explicitly enabled bars on pipes and CI emit clean log lines.
            self.file.write(_strip_ansi(styled) + "\n")
            self.file.flush()

    # ----- pandas integration ------------------------------------------

    @classmethod
    def pandas(cls, **kwargs: Any) -> None:
        """
        Install a `.progress_apply()` method on pandas Series/DataFrame/groupby
        that uses BrewBar. Mirrors `tqdm.pandas()`.
        """
        try:
            import pandas as pd  # type: ignore
        except ImportError:
            raise ImportError("pandas is not installed")

        from pandas.core.frame import DataFrame  # type: ignore
        from pandas.core.series import Series  # type: ignore
        try:
            from pandas.core.groupby.generic import DataFrameGroupBy, SeriesGroupBy  # type: ignore
        except ImportError:
            DataFrameGroupBy = SeriesGroupBy = None  # type: ignore

        bar_kwargs = kwargs

        def inner_apply(obj_self, func, *args, **fkwargs):
            total = len(obj_self) if hasattr(obj_self, "__len__") else None
            with cls(total=total, **bar_kwargs) as pbar:
                def wrapped(*a, **kw):
                    res = func(*a, **kw)
                    pbar.update(1)
                    return res
                return obj_self.apply(wrapped, *args, **fkwargs)

        Series.progress_apply = inner_apply
        DataFrame.progress_apply = inner_apply
        if DataFrameGroupBy is not None:
            DataFrameGroupBy.progress_apply = inner_apply
            SeriesGroupBy.progress_apply = inner_apply


# ---------------------------------------------------------------------------
# BarGroup — managed multi-bar UI
# ---------------------------------------------------------------------------

class BarGroup:
    """
    Manage a group of bars rendered together. Useful for multi-stage
    pipelines or parallel work with one bar per worker.
    """

    def __init__(self, file: Optional[TextIO] = None):
        self.file = file if file is not None else sys.stderr
        self.bars: List[BrewBar] = []
        self._lock = threading.RLock()

    def add(self, *args: Any, **kwargs: Any) -> BrewBar:
        kwargs.setdefault("file", self.file)
        with self._lock:
            if "position" not in kwargs:
                occupied = [
                    b.position for b in _ACTIVE_BARS
                    if b.file is self.file and not b._closed and not b.disable
                ]
                state = _STREAM_STATES.get(id(self.file))
                if state is not None and state["stream"] is self.file:
                    occupied.extend(state["static_lines"])
                kwargs["position"] = max(occupied, default=-1) + 1
            b = BrewBar(*args, **kwargs)
            self.bars.append(b)
        return b

    def close(self) -> None:
        for b in self.bars:
            b.close()

    def __enter__(self) -> "BarGroup":
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Module-level conveniences
# ---------------------------------------------------------------------------

def bar(*args: Any, **kwargs: Any) -> BrewBar:
    return BrewBar(*args, **kwargs)


def trange(*args: Any, **kwargs: Any) -> BrewBar:
    return BrewBar(range(*args), **kwargs)


def track(iterable: Iterable, **kwargs: Any) -> BrewBar:
    """tqdm.auto-style helper."""
    return BrewBar(iterable, **kwargs)


def track_futures(
    futures: Iterable[Any],
    *,
    timeout: Optional[float] = None,
    **bar_kwargs: Any,
) -> Iterator[Any]:
    """Yield futures in completion order and advance a bar as each finishes."""
    pending = list(futures)
    bar_kwargs.setdefault("total", len(pending))
    pbar = BrewBar(**bar_kwargs)
    try:
        for future in _futures_as_completed(pending, timeout=timeout):
            pbar.update(1)
            yield future
    finally:
        pbar.close()


async def track_tasks(
    awaitables: Iterable[Any],
    *,
    timeout: Optional[float] = None,
    **bar_kwargs: Any,
) -> AsyncIterator[Any]:
    """Await tasks in completion order, yielding results and tracking progress."""
    tasks = [asyncio.ensure_future(awaitable) for awaitable in awaitables]
    pending = set(tasks)
    bar_kwargs.setdefault("total", len(tasks))
    pbar = BrewBar(**bar_kwargs)
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        while pending:
            remaining = None
            if timeout is not None:
                remaining = max(0.0, timeout - (loop.time() - started))
            done, pending = await asyncio.wait(
                pending,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise asyncio.TimeoutError()
            for task in done:
                pbar.update(1)
                yield task.result()
    finally:
        pbar.close()


@contextmanager
def _noop_cm():
    yield


def write(msg: str, file: Optional[TextIO] = None, end: str = "\n", nolock: bool = False) -> None:
    """Print without breaking active bars."""
    out = file if file is not None else sys.stderr
    ctx = _noop_cm() if nolock else _GLOBAL_LOCK
    with ctx:
        is_tty = hasattr(out, "isatty") and out.isatty()
        state = _STREAM_STATES.get(id(out)) if is_tty else None
        if state is not None and state["stream"] is out:
            _clear_drawn_rows(out, state)
        out.write(msg + end)
        out.flush()
        if state is not None and state["stream"] is out:
            _redraw_stream(out)


class _BarWriteHandler(logging.Handler):
    """Logging handler that pipes through write() so bars don't break."""

    def __init__(self, file: Optional[TextIO] = None):
        super().__init__()
        self.file = file

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            write(msg, file=self.file)
        except Exception:
            self.handleError(record)


def redirect_logging(
    level: int = logging.INFO,
    logger: Optional[logging.Logger] = None,
    file: Optional[TextIO] = None,
    fmt: str = "%(asctime)s %(levelname)s %(name)s: %(message)s",
) -> _BarWriteHandler:
    """
    Replace handlers on the given logger (root by default) with one
    that routes through brewbar.write(), so log lines don't break bars.
    """
    target = logger if logger is not None else logging.getLogger()
    handler = _BarWriteHandler(file=file)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt))
    target.handlers = [handler]
    target.setLevel(level)
    return handler
