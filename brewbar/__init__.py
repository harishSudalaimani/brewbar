"""
brewbar — a progress bar library for Python.

A common tqdm-style API with extra features: predictive ETA, memory/CPU
monitoring, rate sparklines, pause/resume timing, hooks, metric tracking,
auto-color, async iteration, completion-order task tracking, multi-bar groups,
structured events, and logging integration.

Quick start:

    from brewbar import bar, trange
    for x in bar(range(100), desc="Working"):
        ...
    for i in trange(100, desc="Loading"):
        ...
"""

from .core import (
    BrewBar,
    BarGroup,
    bar,
    trange,
    track,
    track_futures,
    track_tasks,
    write,
    redirect_logging,
)

# Familiar names for projects migrating from tqdm.
tqdm = BrewBar

__all__ = [
    "BrewBar",
    "tqdm",
    "BarGroup",
    "bar",
    "trange",
    "track",
    "track_futures",
    "track_tasks",
    "write",
    "redirect_logging",
]

__version__ = "2.1.0"


# tqdm-style auto submodule: `from brewbar.auto import bar`
import sys as _sys
import types as _types
_auto = _types.ModuleType("brewbar.auto")
_auto.bar = bar
_auto.trange = trange
_auto.BrewBar = BrewBar
_auto.tqdm = BrewBar  # drop-in alias
_sys.modules["brewbar.auto"] = _auto
