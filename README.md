# 🍺 brewbar

**A progress bar for Python — with beer.** Brewbar supports the familiar tqdm iterator and manual-update patterns, then adds predictive ETA, monitoring, hooks, and metrics. Beer-themed by default; turn it off with `brew=False` for plain blocks.

[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## What's new in 2.1.0

- Added `from brewbar import tqdm` as a familiar migration alias and `format_dict` for formatter integrations.
- Made concurrent `update()` calls safe for shared bars and kept callback execution outside the update lock.
- Improved terminal width calculation and clipping for wide Unicode characters.
- Synchronized the package and runtime version at `2.1.0`.

This is compatibility for common tqdm usage, not a promise that every tqdm backend, notebook widget, or advanced option behaves identically.

---

## The look

```
Working: 100% |🍺🍺🍺🍺🍺🍺🍺🍺🍺🍺🍺🍺🍺🍺🍺🍺🍺🍺🍺🍺🍺| 50/50 [00:00<00:00, 839.06it/s] cheers 🍻
Half:     50% |🍺🍺🍺🍺🍺🍺🍺🍺🍺🍺··········| 50/100 [00:00<00:00, 18.9it/s] fermenting
```

The bar walks through brew stages as it fills: **mashing → boiling → fermenting → conditioning → cheers 🍻**. Want clean Unicode blocks instead? `bar(..., brew=False)`.

---

## Why brewbar?

If you've used tqdm, the common iterator and manual-update patterns will feel familiar. Brewbar also adds:

| Feature | tqdm | brewbar |
|---|---|---|
| Iterator-based bars | ✅ | ✅ |
| Manual `update()` mode | ✅ | ✅ |
| Nested / multi bars | ✅ | ✅ |
| Postfix / descriptions | ✅ | ✅ |
| Dynamic miniters auto-tune | ✅ | ✅ |
| pandas integration | ✅ | ✅ |
| Predictive ETA (regression-based) | ❌ | ✅ |
| ETA confidence intervals | ❌ | ✅ |
| Memory monitoring built-in | ❌ | ✅ |
| CPU monitoring built-in | ❌ | ✅ |
| Rate trend sparkline | ❌ | ✅ |
| Pause / resume timing | ❌ | ✅ |
| Auto-color (green/yellow/red) | ❌ | ✅ |
| Time budget tracking | ❌ | ✅ |
| Hooks (on_update / on_complete / on_interval) | ❌ | ✅ |
| Metric tracking (min/max/avg) | ❌ | ✅ |
| Logging integration | partial | ✅ |
| Multi-bar group manager | ❌ | ✅ |

**Zero hard dependencies.** Optional extras: `psutil` (memory/CPU), `pandas`.

---

## Install

```bash
pip install brewbar              # core only
pip install brewbar[monitoring]  # + memory/CPU
pip install brewbar[pandas]      # + pandas .progress_apply
pip install brewbar[all]         # everything
```

---

## Migrate from tqdm

```python
# from tqdm import tqdm, trange
from brewbar import tqdm, trange

for x in tqdm(range(100), desc="Training"):
    ...

for i in trange(100):
    ...
```

Or use brewbar's class and native helper names:

```python
from brewbar import bar, trange, BrewBar

for x in bar(range(100), desc="Working"):
    ...
```

---

## Basic usage

```python
from brewbar import bar
import time

for _ in bar(range(100), desc="Processing"):
    time.sleep(0.05)
```

```
Processing:  47% |██████████████████▍                    | 47/100 [00:02<00:02, 18.9it/s]
```

### Manual mode

```python
from brewbar import BrewBar

with BrewBar(total=1024, unit="B", unit_scale=True, desc="Download") as pbar:
    for chunk in download():
        pbar.update(len(chunk))
```

### Description + postfix (training loops)

```python
pbar = bar(range(epochs), desc="Train")
for epoch in pbar:
    loss = train_one_epoch()
    pbar.set_postfix(loss=loss, lr=0.001, acc=0.95)
```

---

## Features tqdm doesn't have

### 1. Predictive ETA with confidence

Standard progress bars show ETA based on average rate. brewbar uses linear regression over recent samples, and can show a confidence window:

```python
bar(range(1000), eta_confidence=True)
```

```
Working:  42% |████████████▌                | 420/1000 [00:21<00:30±00:05, 19.8it/s]
```

The `±00:05` tells you how confident the estimate is. Wider window = less stable rate.

### 2. Memory / CPU monitoring

Requires `pip install brewbar[monitoring]`.

```python
bar(range(1000), show_memory=True, show_cpu=True)
```

```
Working:  42% |████████████▌      | 420/1000 [00:21<00:30, 19.8it/s] mem=1.24GiB cpu=87%
```

### 3. Rate trend sparkline

See whether your throughput is climbing or collapsing at a glance:

```python
bar(range(1000), show_sparkline=True)
```

```
 42% |████████████▌      | 420/1000 [00:21<00:30, 19.8it/s] ▁▂▃▅▆▇█▇
```

### 4. Auto-color

Color the bar by pace, no manual logic:

```python
bar(range(1000), auto_color=True)
# green when rate is steady/improving
# yellow when it drops
# red when it tanks
```

Combine with a time budget:

```python
bar(range(1000), auto_color=True, eta_budget=60)
# green if projected completion < 60s, yellow near limit, red if over
```

### 5. Pause / resume timing

I/O waits or user interaction shouldn't count against your throughput. Pause the timer:

```python
pbar = bar(range(100))
for i in pbar:
    process(i)
    with pbar.paused():
        input("Press enter to continue...")
```

Time inside `paused()` is excluded from elapsed/rate/ETA.

### 6. Hooks

```python
def log_progress(b):
    print(f"At {b.n}/{b.total}, rate={b._avg_rate:.1f}/s")

bar(range(1000),
    on_update=lambda b: ...,                    # every render
    on_complete=lambda b: ...,                  # when done
    on_interval=(5.0, log_progress))            # every 5 seconds
```

### 7. Metric tracking

Track stats over the lifetime of the bar:

```python
pbar = bar(range(epochs), track_metrics=["loss", "acc"])
for epoch in pbar:
    pbar.set_postfix(loss=loss(), acc=acc())

print(pbar.metric_summary())
# {'loss': {'min': 0.12, 'max': 2.30, 'avg': 0.87, 'last': 0.12, 'count': 100},
#  'acc':  {'min': 0.41, 'max': 0.96, 'avg': 0.82, 'last': 0.96, 'count': 100}}
```

### 8. Multi-bar groups

```python
from brewbar import BarGroup

with BarGroup() as group:
    download = group.add(total=100, desc="Download")
    process  = group.add(total=100, desc="Process")
    upload   = group.add(total=100, desc="Upload")

    for i in range(100):
        download.update(); process.update(); upload.update()
```

### 9. Logging integration

Route log records through brewbar so they don't shred your bars:

```python
import logging
from brewbar import bar, redirect_logging

redirect_logging(level=logging.INFO)
log = logging.getLogger(__name__)

for i in bar(range(100), desc="Work"):
    if i % 10 == 0:
        log.info(f"checkpoint {i}")   # appears above the bar cleanly
```

### 10. `write()` without breaking bars

```python
from brewbar import bar, write

pbar = bar(range(100))
for i in pbar:
    if some_condition:
        write(f"  >> noteworthy event at {i}")   # or pbar.write(...)
```

---

## Full API reference

### `BrewBar(iterable=None, *, ...)`

All tqdm-compatible kwargs:

`desc`, `total`, `leave`, `file`, `ncols`, `mininterval`, `maxinterval`, `miniters`, `ascii`, `disable`, `unit`, `unit_scale`, `unit_divisor`, `dynamic_ncols`, `smoothing`, `bar_format`, `initial`, `position`, `postfix`, `delay`, `colour` / `color`

brewbar extras:

`brew` (default True — 🍺 fill glyphs and brew stage labels), `show_stage`, `auto_color`, `show_memory`, `show_cpu`, `show_sparkline`, `sparkline_width`, `eta_confidence`, `eta_budget`, `on_update`, `on_complete`, `on_interval`, `track_metrics`

### Methods

`.update(n=1)`, `.refresh()`, `.reset(total=None)`, `.clear()`, `.close()`, `.set_description(desc)`, `.set_description_str(desc)`, `.set_postfix(...)`, `.set_postfix_str(s)`, `.display()`, `.pause()`, `.resume()`, `.paused()`, `.metric_summary()`, `.write(msg)`, `.format_dict`

### Class methods

`BrewBar.get_lock()`, `BrewBar.set_lock(lock)`, `BrewBar.write(msg)`, `BrewBar.pandas(**kw)`

### Module functions

`bar(...)`, `trange(...)`, `track(...)`, `write(...)`, `redirect_logging(...)`

### `bar_format` fields

`{n}` `{n_fmt}` `{total}` `{total_fmt}` `{percentage}` `{rate}` `{rate_fmt}` `{elapsed}` `{elapsed_fmt}` `{remaining}` `{remaining_fmt}` `{remaining_conf_fmt}` `{desc}` `{postfix}` `{unit}` `{bar}` `{memory}` `{cpu}` `{sparkline}`

Example:

```python
bar(range(100),
    bar_format="{desc} {percentage:3.0f}% [{bar}] {n_fmt}/{total_fmt} • {rate_fmt} • ETA {remaining_fmt}",
    ncols=20)
```

---

## Pandas

```python
from brewbar import BrewBar
import pandas as pd

BrewBar.pandas(desc="Computing")
df["squared"] = df["x"].progress_apply(lambda x: x * x)
```

---

## Compatibility

- Python 3.8 — 3.13
- Linux, macOS, Windows (Windows Terminal recommended)
- TTY-aware: silently disables in CI, pipes, and redirected output (override with `disable=False`)
- `NO_COLOR` and `FORCE_COLOR` env vars honored

---

## License

MIT
