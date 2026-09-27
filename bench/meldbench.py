#!/usr/bin/env python3
"""Responsiveness benchmark for Meld folder comparisons.

Runs Meld in-process, opens a folder comparison and drives it through a
series of steps, timing each one and measuring how long the main loop is
blocked while it runs. A 2ms timeout at G_PRIORITY_HIGH records every gap
between its dispatches; a gap much longer than 2ms is time in which Meld
could not have reacted to input or drawn a frame.

It needs a display; bench/in-container provides one with Xvfb.

    bench/in-container python3 bench/meldbench.py LEFT RIGHT [BASE]
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# The Meld tree under test; --source points it at another checkout
SOURCE = REPO

STEPS = ("scan", "expand", "scroll", "cursor", "nextchange", "filter", "rescan")


def load_launcher():
    loader = importlib.machinery.SourceFileLoader(
        "meld_launcher", str(SOURCE / "bin" / "meld")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    launcher = importlib.util.module_from_spec(spec)
    loader.exec_module(launcher)
    return launcher


def setup_meld():
    launcher = load_launcher()
    launcher.setup_process()
    launcher.setup_import_paths()
    launcher.environment_hacks()
    launcher.setup_logging()
    launcher.setup_i18n()
    launcher.check_requirements()
    launcher.setup_glib_logging()
    launcher.setup_resources()
    launcher.setup_settings()
    launcher.setup_style()


class StallMonitor:
    INTERVAL_MS = 2

    def __init__(self):
        from gi.repository import GLib

        self.gaps = []
        self.last = time.perf_counter()
        GLib.timeout_add(self.INTERVAL_MS, self._tick, priority=GLib.PRIORITY_HIGH)

    def _tick(self):
        now = time.perf_counter()
        self.gaps.append(now - self.last)
        self.last = now
        return True

    def mark(self):
        return len(self.gaps)

    def summary(self, since):
        interval = self.INTERVAL_MS / 1000
        blocks = sorted(max(0.0, g - interval) for g in self.gaps[since:])
        if not blocks:
            blocks = [0.0]
        p99 = blocks[min(len(blocks) - 1, int(len(blocks) * 0.99))]
        return {
            "max_block_ms": round(blocks[-1] * 1000, 1),
            "p99_block_ms": round(p99 * 1000, 1),
            # Time spent in blocks long enough to be felt as a stall
            "stalled_ms": round(sum(b for b in blocks if b >= 0.05) * 1000, 1),
            "stalls_over_100ms": sum(1 for b in blocks if b >= 0.1),
        }


class Bench:
    def __init__(self, app, args):
        self.app = app
        self.args = args
        self.results = {}
        self.monitor = None
        self.doc = None
        self.window = None
        self.painted = 0
        self.first_row_time = None
        self.first_paint_time = None
        self.paint_timeouts = 0

    # Coroutine plumbing: the driver is a generator that yields either a
    # predicate to wait for, or a number of seconds to sleep.
    def start(self):
        from gi.repository import GLib

        self.gen = self.drive()
        GLib.idle_add(self._step)
        return False

    def _step(self):
        from gi.repository import GLib

        try:
            cmd = next(self.gen)
        except StopIteration:
            self.app.quit()
            return False
        except Exception:
            import traceback

            traceback.print_exc()
            self.results["error"] = True
            self.app.quit()
            return False
        if callable(cmd):

            def poll():
                if cmd():
                    self._step()
                    return False
                return True

            GLib.timeout_add(2, poll)
        else:
            GLib.timeout_add(int(cmd * 1000), self._step)
        return False

    def scan_done(self):
        return (
            self.doc is not None
            and self.doc._scan_in_progress == 0
            and not self.window.scheduler.tasks_pending()
        )

    def next_paint(self):
        """Return a predicate that is true once a new frame has been painted"""
        target = self.painted + 1
        self.doc.treeview0.queue_draw()
        deadline = time.perf_counter() + 2

        def painted():
            if self.painted >= target:
                return True
            if time.perf_counter() > deadline:
                self.paint_timeouts += 1
                print("  (no frame within 2s)", file=sys.stderr)
                return True
            return False

        return painted

    def _on_after_paint(self, clock):
        self.painted += 1
        if self.first_row_time and not self.first_paint_time:
            self.first_paint_time = time.perf_counter()

    def record(self, name, t0, mark, **extra):
        elapsed = time.perf_counter() - t0
        result = {"ms": round(elapsed * 1000, 1)}
        result.update(extra)
        result.update(self.monitor.summary(mark))
        if self.args.verify:
            result["expanded"] = expanded_rows(self.doc)
            result["chunkmap"] = chunkmap_digests(self.doc)
        self.results[name] = result
        print(f"  {name:12} {json.dumps(result)}", file=sys.stderr, flush=True)

    def timed_scan(self, name, action):
        """Run `action`, which starts a scan, and wait for it to finish"""
        self.first_row_time = None
        self.first_paint_time = None
        mark = self.monitor.mark()
        t0 = time.perf_counter()
        action()
        yield lambda: self.doc is not None
        yield self.scan_done
        done = time.perf_counter()
        yield self.next_paint()
        extra = {"rows": count_rows(self.doc.model)}
        if self.first_row_time:
            extra["first_row_ms"] = round((self.first_row_time - t0) * 1000, 1)
        if self.first_paint_time:
            extra["first_paint_ms"] = round((self.first_paint_time - t0) * 1000, 1)
        self.record(name, t0, mark, **extra)
        self.results[name]["ms"] = round((done - t0) * 1000, 1)

    def drive(self):
        from gi.repository import Gio, GLib, Gtk

        steps = self.args.steps
        yield lambda: self.app.get_active_window() is not None
        self.window = self.app.get_active_window()
        self.window.set_default_size(1600, 1000)
        yield 0.5
        self.window.get_frame_clock().connect("after-paint", self._on_after_paint)
        self.monitor = StallMonitor()

        def open_comparison():
            gfiles = [Gio.File.new_for_path(p) for p in self.args.dirs]

            def opened(tab, error):
                if error:
                    raise error
                self.doc = tab
                tab.model.connect("row-inserted", self._on_row_inserted)

            self.window.open_paths(gfiles, focus=True, on_complete=opened)

        yield from self.timed_scan("scan", open_comparison)
        tv = self.doc.treeview0
        root = Gtk.TreePath.new_first()

        if "expand" in steps or "scroll" in steps or "cursor" in steps:
            tv.grab_focus()
            tv.set_cursor(root)
            yield self.next_paint()
            mark, t0 = self.monitor.mark(), time.perf_counter()
            self.doc.action_folder_expand()
            yield self.next_paint()
            self.record("expand_all", t0, mark, rows=count_rows(self.doc.model))

            if "expand" in steps:
                tv.set_cursor(root)
                mark, t0 = self.monitor.mark(), time.perf_counter()
                self.doc.action_folder_collapse()
                yield self.next_paint()
                self.record("collapse_all", t0, mark)
                if self.args.verify:
                    # Expansions below a collapsed folder must not come back
                    tv.expand_row(root, False)
                    yield self.next_paint()
                    self.results["collapse_all"]["root_reopened"] = expanded_rows(
                        self.doc
                    )
                    tv.collapse_row(root)
                tv.set_cursor(root)
                mark, t0 = self.monitor.mark(), time.perf_counter()
                self.doc.action_folder_expand()
                yield self.next_paint()
                self.record("re_expand", t0, mark)

        if "scroll" in steps:
            vadj = tv.get_vadjustment()
            yield from self.per_frame(
                "scroll_page",
                self.args.moves,
                lambda: vadj.set_value(
                    min(vadj.get_value() + vadj.get_page_size(), vadj.get_upper())
                ),
            )
            mark, t0 = self.monitor.mark(), time.perf_counter()
            vadj.set_value(0)
            yield self.next_paint()
            vadj.set_value(vadj.get_upper())
            yield self.next_paint()
            self.record("scroll_ends", t0, mark)
            vadj.set_value(0)
            yield self.next_paint()

        if "cursor" in steps:
            tv.set_cursor(root)
            yield self.next_paint()
            yield from self.per_frame(
                "cursor_down",
                self.args.moves,
                lambda: tv.emit(
                    "move-cursor", Gtk.MovementStep.DISPLAY_LINES, 1, False, False
                ),
            )
            yield from self.per_frame(
                "cursor_pgdn",
                max(1, self.args.moves // 10),
                lambda: tv.emit("move-cursor", Gtk.MovementStep.PAGES, 1, False, False),
            )

        if "nextchange" in steps:
            tv.set_cursor(root)
            yield self.next_paint()
            yield from self.per_frame(
                "next_change",
                max(1, self.args.moves // 10),
                self.doc.action_next_change,
            )

        if "filter" in steps:
            action = self.doc.view_action_group.lookup_action("folder-status-same")
            yield from self.timed_scan(
                "filter_hide_same",
                lambda: action.change_state(GLib.Variant.new_boolean(False)),
            )
            yield from self.timed_scan(
                "filter_show_same",
                lambda: action.change_state(GLib.Variant.new_boolean(True)),
            )

        if "rescan" in steps:
            if self.args.touch:
                with open(self.args.touch, "a") as f:
                    f.write("/* meldbench */\n")
            yield from self.timed_scan("rescan", self.doc.refresh)

    def per_frame(self, name, count, action):
        """Run `action` `count` times, waiting for a frame after each"""
        mark, t0 = self.monitor.mark(), time.perf_counter()
        latencies = []
        for _ in range(count):
            start = time.perf_counter()
            action()
            yield self.next_paint()
            latencies.append(time.perf_counter() - start)
        latencies.sort()
        self.record(
            name,
            t0,
            mark,
            n=count,
            median_frame_ms=round(latencies[len(latencies) // 2] * 1000, 1),
            max_frame_ms=round(latencies[-1] * 1000, 1),
        )

    def _on_row_inserted(self, model, path, it):
        if self.first_row_time is None and path.get_depth() >= 2:
            self.first_row_time = time.perf_counter()


def expanded_rows(doc):
    """Count the expanded rows in each pane's view, for --verify"""
    counts = []
    for view in doc.treeview[: doc.num_panes]:
        count = 0

        def check(model, path, it, view=view):
            nonlocal count
            count += view.row_expanded(path)

        doc.model.foreach(check)
        counts.append(count)
    return counts


def chunkmap_digests(doc):
    """Digest each pane's chunk map coordinates, for --verify"""
    import hashlib

    return [
        hashlib.sha1(
            repr(sorted(m.chunk_coords_by_tag().items())).encode()
        ).hexdigest()[:12]
        for m in doc.chunkmap[: doc.num_panes]
    ]


def count_rows(model):
    count = 0

    def inc(*args):
        nonlocal count
        count += 1

    model.foreach(inc)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dirs", nargs="+", help="two or three folders to compare")
    parser.add_argument(
        "--steps",
        default=",".join(STEPS),
        help="comma-separated subset of: " + ", ".join(STEPS),
    )
    parser.add_argument("--moves", type=int, default=100, help="scroll/cursor moves")
    parser.add_argument("--touch", help="file to append to before the rescan step")
    parser.add_argument("--out", help="write results as JSON to this file")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="record per-view expanded row counts, to check behaviour (slow)",
    )
    parser.add_argument("--label", default="", help="free-form label for the JSON")
    parser.add_argument(
        "--source",
        type=Path,
        default=REPO,
        help="Meld checkout to run (default: this one)",
    )
    args = parser.parse_args()
    args.steps = set(args.steps.split(","))
    if len(args.dirs) not in (2, 3):
        parser.error("need two or three folders")

    global SOURCE
    SOURCE = args.source.resolve()
    t_start = time.perf_counter()
    os.chdir(SOURCE)
    setup_meld()

    from gi.repository import GLib

    from meld.meldapp import MeldApp

    app = MeldApp()
    bench = Bench(app, args)
    GLib.idle_add(bench.start)
    app.run(["meld"])

    bench.results["paint_timeouts"] = bench.paint_timeouts
    bench.results["total_s"] = round(time.perf_counter() - t_start, 2)
    report = {
        "label": args.label,
        "source": str(SOURCE),
        "dirs": args.dirs,
        "results": bench.results,
    }
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=1)
    print(json.dumps(report))
    return 1 if bench.results.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
