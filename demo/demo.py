"""
demo.py — exercises every brewbar v2 feature.

Run:   python demo.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import random
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from brewbar import (
    BrewBar,
    BarGroup,
    bar,
    trange,
    track,
    write,
    redirect_logging,
)


def section(title: str) -> None:
    print(f"\n--- {title} ---")


def test_basic():
    section("basic iteration")
    for _ in bar(range(50), desc="Working"):
        time.sleep(0.02)


def test_trange():
    section("trange (tqdm-style)")
    for _ in trange(50, desc="Loading"):
        time.sleep(0.02)


def test_manual():
    section("manual update mode")
    with BrewBar(total=50, desc="Manual") as pbar:
        for _ in range(50):
            time.sleep(0.02)
            pbar.update()


def test_unit_scale():
    section("unit_scale (bytes)")
    with BrewBar(total=5_000_000, desc="Download", unit="B",
                 unit_scale=True, unit_divisor=1024) as pbar:
        for _ in range(50):
            time.sleep(0.03)
            pbar.update(100_000)


def test_desc_postfix():
    section("desc + live postfix (training loop)")
    pbar = bar(range(30), desc="Epoch 1")
    for i in pbar:
        time.sleep(0.05)
        pbar.set_postfix(loss=1.0 / (i + 1), acc=0.5 + i / 60, lr=0.001)


def test_predictive_eta():
    section("predictive ETA with confidence")
    # Variable rate to show confidence widening
    pbar = bar(range(80), desc="Variable", eta_confidence=True)
    for i in pbar:
        time.sleep(random.uniform(0.02, 0.08))


def test_sparkline():
    section("rate sparkline")
    pbar = bar(range(100), desc="Throughput", show_sparkline=True, sparkline_width=10)
    for i in pbar:
        # Speed up then slow down
        if i < 50:
            time.sleep(0.02)
        else:
            time.sleep(0.05)


def test_auto_color():
    section("auto-color (rate-based)")
    pbar = bar(range(60), desc="Pacing", auto_color=True)
    for i in pbar:
        # Slow down halfway through
        time.sleep(0.02 if i < 30 else 0.08)


def test_auto_color_budget():
    section("auto-color with time budget")
    # Budget: 2 seconds. Loop takes ~3s, so we should go yellow → red.
    pbar = bar(range(60), desc="Budgeted", auto_color=True, eta_budget=2.0)
    for _ in pbar:
        time.sleep(0.05)


def test_pause_resume():
    section("pause/resume timing")
    pbar = bar(range(20), desc="With pauses")
    for i in pbar:
        time.sleep(0.05)
        if i == 10:
            with pbar.paused():
                time.sleep(0.5)  # this shouldn't count


def test_hooks():
    section("hooks (on_interval)")
    events = []

    def on_tick(b):
        events.append((b.n, b._avg_rate))

    for _ in bar(range(60), desc="Hooked",
                 on_interval=(0.3, on_tick),
                 on_complete=lambda b: write(f"  done: {b.n} items")):
        time.sleep(0.05)
    print(f"  on_interval fired {len(events)} times")


def test_track_metrics():
    section("metric tracking")
    pbar = bar(range(40), desc="Metrics", track_metrics=["loss", "acc"])
    for i in pbar:
        time.sleep(0.03)
        pbar.set_postfix(loss=random.uniform(0.1, 1.0), acc=random.uniform(0.6, 0.99))
    print("  summary:", pbar.metric_summary())


def test_nested():
    section("nested bars")
    for _ in bar(range(3), desc="Outer"):
        for _ in bar(range(20), desc="Inner", leave=False):
            time.sleep(0.02)


def test_bar_group():
    section("BarGroup — managed multi-bar")
    with BarGroup() as g:
        b1 = g.add(total=50, desc="Stage 1")
        b2 = g.add(total=50, desc="Stage 2")
        b3 = g.add(total=50, desc="Stage 3")
        for _ in range(50):
            time.sleep(0.03)
            b1.update(); b2.update(); b3.update()


def test_write_during_loop():
    section("write() — print without breaking bar")
    pbar = bar(range(40), desc="Logging")
    for i in pbar:
        time.sleep(0.03)
        if i % 10 == 9:
            write(f"  checkpoint at {i + 1}")


def test_logging():
    section("logging integration")
    redirect_logging(level=logging.INFO)
    log = logging.getLogger("demo")
    for i in bar(range(30), desc="Logged"):
        time.sleep(0.04)
        if i % 10 == 0 and i > 0:
            log.info(f"step {i}")


def test_bar_format():
    section("custom bar_format")
    fmt = "{desc} {percentage:3.0f}% [{bar}] {n_fmt}/{total_fmt} • {rate_fmt} • {remaining_fmt}"
    for _ in bar(range(40), desc="Custom", bar_format=fmt, ncols=20):
        time.sleep(0.04)


def test_threading():
    section("thread-safe parallel updates")
    with BrewBar(total=200, desc="Parallel") as pbar:
        def work(_):
            time.sleep(0.005)
            pbar.update()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(work, range(200)))


def test_spinner():
    section("spinner mode (unknown total)")
    def stream():
        while True:
            yield None
    pbar = bar(stream(), desc="Streaming")
    for i, _ in enumerate(pbar):
        time.sleep(0.03)
        if i > 30:
            break


def test_reset():
    section("reset across phases")
    b = BrewBar(total=20, desc="Phase 1")
    for _ in range(20):
        time.sleep(0.03)
        b.update()
    b.reset(total=30)
    b.set_description("Phase 2")
    for _ in range(30):
        time.sleep(0.03)
        b.update()
    b.close()


def test_initial():
    section("resume from initial=...")
    with BrewBar(total=100, initial=70, desc="Resumed") as b:
        for _ in range(30):
            time.sleep(0.03)
            b.update()


def test_ascii():
    section("ascii mode")
    for _ in bar(range(30), desc="ASCII", ascii=True):
        time.sleep(0.03)


def test_color():
    section("explicit color")
    for c in ("cyan", "green", "yellow", "magenta"):
        for _ in bar(range(15), desc=f"color={c}", color=c, leave=True):
            time.sleep(0.02)


def test_memory_cpu():
    section("memory / CPU monitoring (requires psutil)")
    try:
        import psutil  # noqa
    except ImportError:
        print("  psutil not installed, skipping")
        return
    data = []
    for _ in bar(range(40), desc="Monitored",
                 show_memory=True, show_cpu=True):
        # allocate to make memory visible
        data.append([0] * 100_000)
        time.sleep(0.04)


def test_empty():
    section("empty iterable")
    for _ in bar(range(0), desc="Empty"):
        pass
    print("  (no bar rendered — correct)")


def test_disable():
    section("disable=True")
    for _ in bar(range(20), disable=True):
        time.sleep(0.02)
    print("  (no output — correct)")


def main():
    # Some tests don't take elapsed_fmt etc., remove safely.
    test_basic()
    test_trange()
    test_manual()
    test_unit_scale()
    test_desc_postfix()
    test_predictive_eta()
    test_sparkline()
    test_auto_color()
    test_auto_color_budget()
    test_pause_resume()
    test_hooks()
    test_track_metrics()
    test_nested()
    test_bar_group()
    test_write_during_loop()
    test_logging()
    test_bar_format()
    test_threading()
    test_spinner()
    test_reset()
    test_initial()
    test_ascii()
    test_color()
    test_memory_cpu()
    test_empty()
    test_disable()

    print("\nAll brewbar tests completed.\n")


if __name__ == "__main__":
    main()
