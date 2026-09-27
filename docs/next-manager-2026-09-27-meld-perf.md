# Handoff: meld folder-compare performance (2026-09-27)

Full report with before/after tables: ~/.claude/claude-control/results/20260927-211435-meld-PerkyPigeon.md

Done: 7 theme branches plus `perf-all` (stacked), all pushed to origin (hila-shemer/meld). Each
commit message carries its numbers. The harness lives on `bench-harness` (this branch), see
bench/README.md. Every branch's tests pass (the 4 test_style errors are upstream's too).

Half-done / known leads:
- 3-way 147k-row synthetic: rescan spends ~45s in 50-100ms main-loop turns (the first scan
  doesn't). Mostly GTK C code, with Python cell renderers visible. Next action: run
  `py-spy record --native` (blocking mode; `--nonblocking` can't do native) on
  `meldbench.py --steps scan,rescan` over ~/.cache/bld/meld/syn100k/{left,base,right} with
  --source ~/.cache/bld/meld/wt/all.
- ~0.6s block at the end of a scan (expand-to-diffs plus the first diff-path walk).
- Chunk map still re-walks every visible row after any change; make it incremental.
- File-diff benchmarks (startup, big file diffs, typing latency): not started. Deprioritized
  by Hila's "mostly about large number of files" re-weight.

Pitfalls: the docker build needs --network host; xvfb-run needs --init; comparisons must be
opened with focus=True; timings are noisy (±10%) while other fleet jobs run, so use interleaved
medians (~/.cache/bld/meld/ab.sh). Everything in ~/.cache/bld/meld (trees, worktrees, results)
is tmpfs and gone after a reboot.
