---
name: tpy-thir-design
description: Run one THIR design-track round -- sweep the design-territory/avoid-list queue, VERIFY each item's premise against the code, batch parallel decision memos for the real design items, and serialize compact approve/park decisions to the user. Analysis-only counterpart to /tpy-thir-wave; approved designs drop back into the grind track as ordinary cells.
---

# /tpy-thir-design

Run one design-track round: inventory -> verify -> memo batch ->
synthesis -> user decisions. This is the analysis-only counterpart to
`/tpy-thir-wave`: the wave skill's completion contract stops at
design-territory / avoid-list / design-fork items; this skill batches
their ANALYSIS autonomously and serializes the DECISIONS to the user.
Per CLAUDE.md, no design decision is ever made autonomously -- this
skill's whole autonomy budget is spent on evidence, options, and
recommendations, never on implementation.

## Arguments

Optional focus. Defaults to a full-queue round.

- `/tpy-thir-design` -- sweep the queue, memo the top 2-3 items
- `/tpy-thir-design extern-C` -- memo one named item
- `/tpy-thir-design sequencing` -- refresh only the avoid-list
  sequencing brief

## The prime directive: verify the premise first

The design track's round-1/2 result (2026-07-22): **five of six
"design-territory" items dissolved or shrank on verification** --
stale interlock claims (a predicate that had long since opened),
refuted framings (renders that already existed), half-stale
"architectural" labels (machinery already landed). A memo that
designs against an unverified premise wastes the user's decision
time. Therefore, before ANY option is drafted:

- Read the actual gate/predicate and QUOTE its conditions -- do not
  trust TODO/memory tags (they are lossy and they rot).
- Probe IN-CONTEXT: predicates like `_f1_record` read the compiler
  ContextVar (`type_def_of`, `native_cpp_names`), so a naive
  out-of-context probe FALSE-NEGATIVES -- always probe inside
  `activate_compiler` (see the wave skill's `scripts/` for the
  pattern; a context-correct probe template survives at
  `/tmp/agents/wave-next/probe_f1_live.py` while it lasts).
- Read the AST oracle (`expected/*.cpp`) for every claimed witness --
  the render the design must reproduce is evidence, not the tag.
- If the premise dissolves, SAY SO LOUDLY, re-tag the item as
  grindable with honest pricing, and correct the stale record
  (TODO.md/ledger/memory) as part of the round's output.

## Operating constraints

- **Analysis-only.** No edits to `tpyc/`, no snapshot regeneration,
  no implementation -- ever, regardless of how mechanical an approved
  fix looks. Doc/queue/memory updates are the only writes.
- **Coordination.** A `/tpy-thir-wave` session may be running in a
  parallel worktree. Stay read-only on shared repo files it may
  touch (TODO.md, BUGS.md, ledger) -- record corrections and BUGS
  candidates in the design-track MEMORY instead, marked "pending
  verification / pending safe window", and let the next safe session
  land them. Memory is the cross-session channel; a session that is
  already running will NOT see updates -- do not assume it will.
- **CPU-light.** No suite runs, no full-corpus probes. Reading code,
  reading snapshots, and single-case in-context probes only. The
  fresh `blockers.json` from the last wave is the sizing source;
  caveat every size with its snapshot age.

## Steps

### 1. Inventory

Sweep the four sources into one queue: TODO.md (grep
`design|DESIGNED|AVOID-LIST|/tpy-add-feature candidate|architectural`),
`docs/THIR_COMPLETION_LEDGER.md` deferred cells, the design-track
memory (`project_thir_design_track.md` -- prior rounds' outcomes and
standing approvals live there; do not re-memo decided items), and the
latest `blockers.json` for sizes. Triage each item:

1. **Designed lever** -> candidate for a decision memo.
2. **Multi-arm-but-not-design** (every render has an oracle, just
   expensive) -> re-tag for the wave track with per-flip pricing; no
   memo needed.
3. **Architectural cluster** -> sequencing brief material (when to
   open, not how to build).

### 2. Memo batch (parallel, fresh-context agents)

Pick the top 2-3 category-1 items by (flip yield x unlock breadth x
decision urgency). Dispatch ONE general-purpose agent per item, in a
single message, read-only, each producing a memo with EXACTLY these
sections (under 60 lines):

1. **PROBLEM** -- the verified mechanism: where the reject fires,
   what the AST does instead (quoted), premise confirmed or refuted.
2. **EVIDENCE** -- witnesses with their oracle renders; flag cases
   carrying OTHER blockers (they flip nothing alone).
3. **YIELD** -- honest flips per option; expect the usual 3-4x shrink
   on drilling; separate direct from interlocked.
4. **OPTIONS** -- 2-3 designs with cost/risk/blast-radius; label each
   design vs arm-widening; count call sites for predicate changes.
5. **RECOMMENDATION** -- one option, the first increment, and the
   pins it needs (routing + byte-identity + BOUNDARY, incl.
   soundness-shaped pins like use-after-move).

Instruct every agent: honesty over completeness -- "this is not
actually design territory" is a first-class (and historically the
most valuable) finding.

### 3. Sequencing brief (when in scope)

For architectural clusters, the brief format is per cluster: SIZE
(cases, with snapshot age), WHY-ARCHITECTURAL (verified against code,
not folklore), OPENS-WHEN (the concrete maturity signal), FIRST-PROBE
(the single cheapest check a future session runs before believing the
brief). End with a recommended order. Sink clusters (ones that
consume several other lanes -- e.g. asyncio) open LAST, always; an
early sink is the big-blob anti-pattern wearing a different hat.

### 4. Synthesize and bank

Fold the memos into the round's outcome: the verified-grindable
backlog (with pricing), the genuine design residue, BUGS candidates
surfaced (marked pending-verification), and stale records to correct.
Bank ALL of it into the design-track memory
(`project_thir_design_track.md` + the MEMORY.md index line) BEFORE
presenting -- the next wave session reads memory at startup; chat
does not survive.

### 5. Serialize decisions to the user

Present the synthesis, then ask about each pending decision ONE AT A
TIME via AskUserQuestion (the user's expressed preference): a short
explanation of what approval means, the recommended option first and
marked, plus park/alternative options. If the user asks for a better
explanation (or rejects the question), STOP asking: teach the
decision properly first -- worked code examples, what each option
means mechanically, what approval commits to -- and then let the user
answer in their own words; never re-ask the same question immediately
after a rejection. Record each answer in the design-track memory as a
STANDING APPROVAL the wave track may implement without re-asking
(quote the approved shape and its boundary rejects precisely -- the
approval covers that shape, not a looser one).

### 6. Wrap

Report the round: what dissolved, what is now grindable (and
pre-approved), what genuinely remains, and the recommended target for
the next wave. If repo files need corrections (stale TODO claims,
BUGS entries), either land them now when no wave session is running,
or leave them in memory marked for the next safe window -- never race
a parallel session for shared files.

## Relationship to the other skills

- `/tpy-thir-wave` grinds; its completion contract stops at what this
  skill handles. Approved designs from step 5 re-enter the wave as
  ordinary cells (the approval satisfies the design gate).
- `/tpy-add-feature` remains the deep procedure for implementing a
  genuinely novel feature; a memo's RECOMMENDATION may explicitly
  hand its design-heavy slice to it (as capture-B3's subject
  materialization did).
- BUGS candidates surfaced here follow CLAUDE.md's bug-report
  discipline once verified -- check for an existing entry, then file.
