#!/usr/bin/env python3
"""Print a before/after table from two or more meldbench.py JSON outputs.

When several files are given for one side (repeated runs), the median of
each metric is used.

    bench/compare.py before.json after.json
    bench/compare.py --before a1.json a2.json --after b1.json b2.json
"""

import argparse
import json
import statistics

METRICS = (
    "ms",
    "first_row_ms",
    "first_paint_ms",
    "median_frame_ms",
    "max_frame_ms",
    "max_block_ms",
    "stalled_ms",
)


def load(paths):
    runs = []
    for path in paths:
        with open(path) as f:
            runs.append(json.load(f)["results"])
    merged = {}
    for step in runs[0]:
        if not isinstance(runs[0][step], dict):
            continue
        merged[step] = {}
        for metric in METRICS:
            values = [r[step][metric] for r in runs if metric in r.get(step, {})]
            if values:
                merged[step][metric] = statistics.median(values)
    return merged


def fmt(value):
    if value is None:
        return "-"
    return f"{value:.0f}" if value >= 10 else f"{value:.1f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*")
    parser.add_argument("--before", nargs="+")
    parser.add_argument("--after", nargs="+")
    parser.add_argument("--metrics", default="ms,first_row_ms,max_block_ms,stalled_ms")
    args = parser.parse_args()
    before = args.before or args.files[:1]
    after = args.after or args.files[1:]
    old, new = load(before), load(after)
    metrics = args.metrics.split(",")

    print("| step | metric | before | after | change |")
    print("|---|---|---:|---:|---:|")
    for step in old:
        for metric in metrics:
            a = old[step].get(metric)
            b = new.get(step, {}).get(metric)
            if a is None and b is None:
                continue
            if a and b is not None:
                change = f"{(b - a) / a * 100:+.0f}%"
            else:
                change = "-"
            print(f"| {step} | {metric} | {fmt(a)} | {fmt(b)} | {change} |")


if __name__ == "__main__":
    main()
