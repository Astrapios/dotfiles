---
name: long-run
description: Launch and babysit a compute job expected to take more than about ten minutes (simulations, multi-collect reruns, deck builds, large pytest runs, PDF conversions): memory cap, smoke test, background launch, progress check-ins every ten minutes with Pacific timestamps, and a faithful completion report. Use when a command will run long, when the user asks to run a sim or batch, or when a background task needs monitoring.
allowed-tools: Read, Bash, Write, Edit, Grep, Glob, ScheduleWakeup, Monitor
---

# Long-running jobs

1. **Estimate before running.** Anything over ~30 s deserves a size estimate; crop or subsample
   when the question allows it. **Smoke-test end to end on a tiny input first**, persist results
   before any cleanup code runs, and derive the full-run ETA from the smoke run.
2. **Known optimizations stay ON.** Every fast path, cache and env flag the repo or you built is
   enabled for every run, above all for benchmarks and "why is this slow" investigations; a
   measurement with a known speedup off is not a measurement. Flags that matter are default-on
   in code, not opt-in per script.
3. **Cap memory.** Compute commands run under
   `prlimit --as=$((220*1024*1024*1024)) pixi run -e dev python ...` (explicit bytes; the `G`
   suffix misparses). GPU allocations do not count against it. Max four concurrent CPHD readers.
4. **Launch in the background** with the Bash tool's `run_in_background`, never a trailing `&`
   inside a foreground call (pipes close, workers die with BrokenPipeError). Multi-GPU work
   reuses the spawn-Pool pattern (`mp.get_context("spawn").Pool(n_gpu)`, `CUDA_VISIBLE_DEVICES`
   set before the cupy import, in-memory combine), never shard/combine subprocess orchestrators.
5. **Check in every ten minutes.** `ScheduleWakeup` (~570 s) paces the check-ins; each report
   names the step or collect done, timestamps, errors, and the updated ETA. Liveness via
   `pgrep -af '[p]attern'` plus `nvidia-smi` memory and log growth (`ps aux` is truncated by the
   rtk wrapper; never kill or relaunch on a bare `ps | grep` miss). Keep a `pkill` in its own
   Bash call with the pattern bracketed so the call does not match itself.
6. **Times are Pacific, 12-hour, with zone**: `TZ=America/Los_Angeles date "+%-I:%M %p %Z"`
   run in the same turn, never extrapolated or converted by hand.
7. **Report faithfully.** A running command is not finished work; read the output you asked
   for, and treat contradicting evidence (a flag printing 0) as signal. Deliver the visual with
   the number in the same turn (`astra send-photo`).
