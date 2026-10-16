import asyncio
import io
import json
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from brewbar import BrewBar, BarGroup, bar, track_futures, track_tasks, tqdm, trange, write


class TTYBuffer(io.StringIO):
    def isatty(self):
        return True


def terminal_rows(output):
    """Apply the small ANSI subset used by BrewBar to a screen buffer."""
    rows = [[]]
    row = column = 0
    i = 0
    while i < len(output):
        char = output[i]
        if char == "\033" and i + 1 < len(output) and output[i + 1] == "[":
            i += 2
            sequence = ""
            while i < len(output) and not ("@" <= output[i] <= "~"):
                sequence += output[i]
                i += 1
            command = output[i] if i < len(output) else ""
            i += 1
            if command == "A":
                row = max(0, row - int(sequence or "1"))
            elif command == "K":
                rows[row] = []
                column = 0
            continue
        if char == "\r":
            column = 0
        elif char == "\n":
            row += 1
            while len(rows) <= row:
                rows.append([])
            column = 0
        else:
            while len(rows[row]) <= column:
                rows[row].append(" ")
            rows[row][column] = char
            column += 1
        i += 1
    return ["".join(line).rstrip() for line in rows]


class CompatibilityTests(unittest.TestCase):
    def test_common_tqdm_names_and_range(self):
        self.assertIs(tqdm, BrewBar)
        self.assertEqual(list(trange(3, disable=True)), [0, 1, 2])

    def test_iterator_and_manual_update(self):
        output = io.StringIO()
        wrapped = bar(["a", "b", "c"], file=output, disable=False, mininterval=0)
        self.assertEqual(list(wrapped), ["a", "b", "c"])
        self.assertEqual(wrapped.n, 3)
        self.assertNotIn("\r", output.getvalue())

        manual = BrewBar(total=4, initial=1, disable=True)
        manual.update(2)
        self.assertEqual(manual.format_dict["n"], 1)

    def test_redirected_output_is_plain_and_events_are_json_friendly(self):
        output = io.StringIO()
        events = []
        pbar = BrewBar(
            total=2,
            file=output,
            disable=False,
            mininterval=0,
            on_event=events.append,
        )
        pbar.update()
        pbar.update()
        pbar.close()

        self.assertNotIn("\r", output.getvalue())
        self.assertNotIn("\033", output.getvalue())
        self.assertEqual([event["event"] for event in events], ["update", "update", "complete"])
        self.assertEqual(events[-1]["n"], 2)
        json.dumps(events)

    def test_ci_environment_can_disable_bar(self):
        with mock.patch.dict(os.environ, {"BREWBAR_DISABLE": "yes"}):
            pbar = BrewBar(total=1, file=io.StringIO())
            self.assertTrue(pbar.disable)
            pbar.close()

    def test_events_include_delayed_and_paused_counter_changes(self):
        events = []
        pbar = BrewBar(
            total=3,
            file=io.StringIO(),
            disable=False,
            delay=60,
            on_event=events.append,
        )
        pbar.update()
        with pbar.paused():
            pbar.update()
        pbar.close()
        self.assertEqual([event["event"] for event in events], ["update", "update", "complete"])
        self.assertEqual(events[-1]["n"], 2)

    def test_grouped_bars_render_as_rows_and_log_above_them(self):
        output = TTYBuffer()
        group = BarGroup(file=output)
        outer = group.add(total=2, desc="outer", mininterval=0, leave=True)
        inner = group.add(total=2, desc="inner", mininterval=0, leave=False)
        outer.update()
        inner.update()
        write("checkpoint", file=output)
        inner.close()

        rows = terminal_rows(output.getvalue())
        self.assertIn("checkpoint", rows[0])
        self.assertTrue(any("outer" in line for line in rows))
        self.assertFalse(any("inner" in line for line in rows))
        group.close()

    def test_nested_leave_false_clears_child_row(self):
        output = TTYBuffer()
        parent = BrewBar(total=2, desc="parent", file=output, mininterval=0, leave=True)
        parent.update()
        child = BrewBar(total=1, desc="child", file=output, mininterval=0, leave=False)
        child.update()
        child.close()

        rows = terminal_rows(output.getvalue())
        self.assertTrue(any("parent" in line for line in rows))
        self.assertFalse(any("child" in line for line in rows))
        parent.close()

    def test_futures_are_yielded_in_completion_order(self):
        events = []
        delays = [(0, 0.04), (1, 0.01), (2, 0.02)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(lambda item, delay: (time.sleep(delay), item)[1], item, delay)
                       for item, delay in delays]
            completed = list(track_futures(
                futures,
                desc="futures",
                file=io.StringIO(),
                disable=False,
                mininterval=0,
                on_event=events.append,
            ))
        self.assertEqual([future.result() for future in completed], [1, 2, 0])
        self.assertEqual(events[-1]["event"], "complete")
        self.assertEqual(events[-1]["n"], 3)


class AsyncCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_iterable_and_async_context_manager(self):
        async def source():
            for value in range(3):
                yield value

        pbar = BrewBar(source(), file=io.StringIO(), disable=False, mininterval=0)
        self.assertEqual([value async for value in pbar], [0, 1, 2])
        self.assertEqual(pbar.n, 3)

        async with BrewBar(total=1, file=io.StringIO(), disable=True) as manual:
            manual.update()
        self.assertTrue(manual._closed)

    async def test_async_tasks_are_yielded_in_completion_order(self):
        async def work(value, delay):
            await asyncio.sleep(delay)
            return value

        events = []
        results = [
            result async for result in track_tasks(
                [work(0, 0.04), work(1, 0.01), work(2, 0.02)],
                desc="tasks",
                file=io.StringIO(),
                disable=False,
                mininterval=0,
                on_event=events.append,
            )
        ]
        self.assertEqual(results, [1, 2, 0])
        self.assertEqual(events[-1]["event"], "complete")
        self.assertEqual(events[-1]["n"], 3)


if __name__ == "__main__":
    unittest.main()
