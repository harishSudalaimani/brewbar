"""Small dependency-free benchmark for BrewBar's per-item overhead."""

import argparse
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brewbar import BrewBar


def measure(label, operation, iterations, repeats):
    durations = []
    for _ in range(repeats):
        started = time.perf_counter()
        operation(iterations)
        durations.append(time.perf_counter() - started)
    elapsed = min(durations)
    rate = iterations / elapsed if elapsed else float("inf")
    print("{:<24} {:>9.4f}s  {:>12,.0f} items/s".format(label, elapsed, rate))
    return elapsed


def plain_loop(iterations):
    for _ in range(iterations):
        pass


def disabled_bar(iterations):
    for _ in BrewBar(range(iterations), disable=True):
        pass


def throttled_bar(iterations):
    output = io.StringIO()
    pbar = BrewBar(
        total=iterations,
        file=output,
        disable=False,
        mininterval=60.0,
        miniters=iterations + 1,
        brew=False,
    )
    for _ in range(iterations):
        pbar.update()
    pbar.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100000)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.iterations <= 0 or args.repeats <= 0:
        parser.error("iterations and repeats must be positive")

    print("BrewBar update overhead (lower is faster; best of {} runs)".format(args.repeats))
    measure("plain loop", plain_loop, args.iterations, args.repeats)
    measure("disabled wrapper", disabled_bar, args.iterations, args.repeats)
    measure("throttled update", throttled_bar, args.iterations, args.repeats)


if __name__ == "__main__":
    main()
