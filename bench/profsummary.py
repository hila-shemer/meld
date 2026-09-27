#!/usr/bin/env python3
"""Summarise a py-spy `--format raw` (collapsed stacks) profile.

Prints the functions with the most inclusive and self samples, only
counting stacks that pass through the optional --under function.

    bench/profsummary.py prof.txt --under _search_recursively_iter
"""

import argparse
import collections


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("--under", help="only count stacks through this function")
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()

    inclusive = collections.Counter()
    own = collections.Counter()
    total = 0
    with open(args.profile) as f:
        for line in f:
            stack, _, count = line.rstrip().rpartition(" ")
            frames = stack.split(";")
            if args.under and not any(args.under in fr for fr in frames):
                continue
            n = int(count)
            total += n
            for frame in set(frames):
                inclusive[frame] += n
            own[frames[-1]] += n

    print(f"{total} samples")
    for title, counter in (("inclusive", inclusive), ("self", own)):
        print(f"\n== {title}")
        for frame, n in counter.most_common(args.top):
            print(f"{n * 100 / total:6.1f}%  {frame}")


if __name__ == "__main__":
    main()
