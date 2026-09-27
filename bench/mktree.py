#!/usr/bin/env python3
"""Generate a pair (or triple) of similar directory trees for benchmarking.

The trees are deterministic for a given seed. The right-hand tree is a
copy of the left with a small number of files modified (same size and
different size), added and removed, and every file gets a different
mtime so that folder comparison has to look at contents, just as it
does for two separate checkouts of the same project.
"""

import argparse
import os
import random
import shutil


def words(rng, n):
    return " ".join(rng.choice(VOCAB) for _ in range(n))


VOCAB = [
    "static",
    "int",
    "struct",
    "void",
    "return",
    "if",
    "else",
    "for",
    "while",
    "unsigned",
    "long",
    "char",
    "const",
    "case",
    "switch",
    "break",
    "goto",
    "err",
    "out",
    "device",
    "driver",
    "kernel",
    "buffer",
]


def make_tree(root, files, fanout, rng):
    """Create `files` files spread over directories `fanout` wide"""
    os.makedirs(root)
    dirs = [root]
    made = 0
    queue = [root]
    while made < files:
        parent = queue.pop(0)
        subdirs = [os.path.join(parent, f"d{i:03d}") for i in range(fanout)]
        for d in subdirs:
            os.mkdir(d)
        dirs.extend(subdirs)
        queue.extend(subdirs)
        for i in range(min(fanout * 4, files - made)):
            path = os.path.join(parent, f"f{i:04d}.c")
            with open(path, "w") as f:
                f.writelines(
                    words(rng, rng.randint(2, 10)) + "\n"
                    for _ in range(rng.randint(5, 60))
                )
            made += 1
    return dirs


def perturb(root, fraction, rng):
    all_files = []
    for dirpath, _dirnames, filenames in os.walk(root):
        all_files.extend(os.path.join(dirpath, f) for f in filenames)
    all_files.sort()
    n = max(1, int(len(all_files) * fraction))
    chosen = rng.sample(all_files, min(len(all_files), 4 * n))
    same_size, grow, remove, add_next_to = (
        chosen[:n],
        chosen[n : 2 * n],
        chosen[2 * n : 3 * n],
        chosen[3 * n :],
    )
    for path in same_size:
        with open(path, "r+b") as f:
            data = bytearray(f.read())
            data[len(data) // 2] ^= 0x20
            f.seek(0)
            f.write(data)
    for path in grow:
        with open(path, "a") as f:
            f.write("/* local change */\n")
    for path in remove:
        os.unlink(path)
    for path in add_next_to:
        with open(path + ".new", "w") as f:
            f.write("added\n")
    return len(same_size) + len(grow), len(remove), len(add_next_to)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dest", help="directory to create the trees in")
    parser.add_argument("--files", type=int, default=100000)
    parser.add_argument("--fanout", type=int, default=8)
    parser.add_argument(
        "--changed", type=float, default=0.002, help="fraction of files to change"
    )
    parser.add_argument("--three", action="store_true", help="also make a third tree")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    left = os.path.join(args.dest, "left")
    if os.path.exists(args.dest):
        shutil.rmtree(args.dest)
    dirs = make_tree(left, args.files, args.fanout, rng)

    others = ["right", "base"] if args.three else ["right"]
    for i, name in enumerate(others):
        other = os.path.join(args.dest, name)
        # copytree without copy2 so that every mtime differs from the left
        shutil.copytree(left, other, copy_function=shutil.copyfile)
        changed = perturb(other, args.changed, random.Random(args.seed + i + 1))
        print(
            f"{name}: modified {changed[0]}, removed {changed[1]}, added {changed[2]}"
        )
    print(f"{args.files} files in {len(dirs)} directories per tree")


if __name__ == "__main__":
    main()
