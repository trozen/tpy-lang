---
name: tpy-fuzzy-test
description: User-perspective fuzz testing of a TPy language feature. Dispatches one or more probe agents (depending on feature breadth) that write and run test programs from a naive-user vantage point (no compiler-source access), then aggregates findings into a unified report.
disable-model-invocation: true
---

# /tpy-fuzzy-test

Fuzz-test a TurboPython language feature from the user perspective. Dispatches `tpy-fuzz-probe` agents that act as naive users: they read docs, brainstorm scenarios, write probe programs, run them, and report what worked / what broke / what surprised them. They do NOT read compiler source.

## Arguments

`$ARGUMENTS` -- natural-language description of what to test.

Examples:
- `/tpy-fuzzy-test tuples` -- broad; split across sub-features
- `/tpy-fuzzy-test Optional narrowing in match statements` -- narrow; one agent
- `/tpy-fuzzy-test @error_return propagation` -- moderate
- `/tpy-fuzzy-test async with` -- moderate

## Path discipline (you must obey, too)

You decide the split, so you also must act as a user when reading. Do NOT read `tpyc/**` or `runtime/cpp/**` while planning. If you find yourself needing to look at the compiler implementation to decide the split, you're overthinking it -- the split should be based on documented sub-features, not implementation layout.

## Steps

### 1. Understand the feature

Read documented behavior in this order:
- `docs/TPY_FOR_AGENTS.md` -- primary source. This is the bootstrap guide for coding agents writing TPy code, which matches the probe agents' perspective. Start here.
- `docs/LANGUAGE_FEATURES.md` -- consult only when you need depth on a feature's status (Working / Planned / Open) or surface that the agent guide doesn't cover.
- Relevant design docs only if the feature has dedicated coverage (e.g. `docs/PROTOCOL_DESIGN.md`, `docs/ASYNC_DESIGN.md`, `docs/ERROR_RETURN_DESIGN.md`, `docs/READONLY_DESIGN.md`, `docs/MACRO_DESIGN.md`).
- `BUGS.md` for any known issues in this area (so you flag known-broken behaviors before agents waste time on them; the report should note "this hits known issue X").

### 2. Decide the split

- **One agent** if the feature is narrow: one concept, one usage shape (e.g. "Optional narrowing in match guards").
- **Multiple agents (2-6)** if the feature is broad. Split along *documented* sub-features, not implementation lines.

Example splits:
| Feature | Sub-features |
|---|---|
| tuples | literals + indexing; unpacking (assign + for); as fields/in containers; with Optional/Union; generic params |
| Optional | narrowing in `if`; narrowing in `match`; with collections; as field; chained `?.` style |
| match | literal patterns; class patterns; sequence patterns; guards + exhaustiveness; with Optional |
| @error_return | basic propagation; nested calls; with collections/comprehensions; in methods/ctors |
| async | basic await + sleep; gather + wait_for (v1.5); cancellation; async with / async for |
| protocols | structural conformance; @dynamic + Adapter/RefAdapter; with generic methods |

State the split in your response before dispatching, so the user can intervene if it's wrong.

### 3. Create the workspace

```bash
ts=$(date +%Y%m%d-%H%M%S)
slug=<sanitized-arg, lowercase, alphanumeric+dashes only>
base=/tmp/agents/fuzz-${slug}-${ts}
mkdir -p ${base}
```

For each sub-feature, also `mkdir -p ${base}/<sub-feature-slug>/`. Pass each agent its own dedicated subdir.

### 4. Fan out probe agents IN PARALLEL

Send **one message with multiple `Agent` tool calls** -- one per sub-feature. Each call uses `subagent_type: tpy-fuzz-probe` and a prompt like:

```
Sub-feature scope: <one-line description>
Probe directory: <abs path to dedicated subdir>
Repo base: /home/tomek/dev/turbo-python/tpy-poc

Follow your documented process and path discipline. Write 5-10 probes mixing
positive + negative scenarios. Run each with TPy and (when applicable) CPython.
Output in your documented format.
```

Single-agent case: just one Agent call (no parallelism needed).

### 5. Aggregate findings

Concatenate all agents' reports, then synthesize:

- **Combined counts** across all sub-features
- **Findings grouped by severity**:
  - Critical: `✗ compile-error`, `⚠ panic`, `! should-be-rejected`
  - Warning: `⚡ diverges-cpython`, `? surprising / undocumented`
  - Info: success count (don't list individual passes; just summarize)
- **UX feedback section**: merge each agent's UX subsections into a single block. Dedupe near-identical complaints across sub-features (e.g. if multiple agents flag the same docs gap, list it once with a count of how many sub-features hit it). Keep UX feedback distinct from findings -- it's about the experience of writing TPy, not pass/fail outcomes.
- **Cross-reference BUGS.md**: if a finding matches a known bug, note "matches BUGS.md entry: <short ref>". Don't suppress -- the report should still surface it so the user sees the reproducer.

### 6. Present the report

```
# /tpy-fuzzy-test report: <feature>

Workspace: /tmp/agents/fuzz-<...>/
Sub-features dispatched: <list>

## Summary
Total probes: <N>
✓ <N>  ✗ <N>  ⚠ <N>  ⚡ <N>  ? <N>  ! <N>

## Critical findings
[grouped by category; each entry references the probe file by full path]

## Warning findings
[same]

## Passed
<N> scenarios across <M> sub-features.

## UX feedback
[merged across agents, grouped by Docs gaps / Confusing diagnostics / Intuition divergence / Ergonomics / Workflow friction; omit empty subsections]

## Suggested follow-up
- Promote <probe path> to a new test case under tests/cases/<group>/<name>/ -- it's a clean repro of a real failure
- File BUGS.md entry for <finding> if not already tracked
- Update `docs/TPY_FOR_AGENTS.md` section <X> -- multiple agents flagged the same gap
- Re-run with a wider scope: /tpy-fuzzy-test <suggested expansion>
```

If everything passed: `# /tpy-fuzzy-test report: all <N> scenarios passed across <M> sub-features. Workspace: <path>`

## Important

- Probes stay in `/tmp/agents/`. Do NOT add to `tests/cases/`. The user decides what to promote.
- Do NOT modify any project file. This skill writes only to `/tmp/agents/`.
- Do NOT run `uv run pytest` or `tests/update_snapshots.py`.
- Do NOT read `tpyc/**` or `runtime/cpp/**` -- same discipline as the probe agents.
- If a probe agent surfaces a finding you suspect is wrong (e.g. the prediction was incorrect), don't quietly drop it -- include it in the report with a note "(agent prediction may be off, manual review needed)".
