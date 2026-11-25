---
name: tpy-design-walk
description: Walk the user through a proposed design or analysis (from /tpy-add-feature, /tpy-fix-bug or a re-plan), one point at a time -- what will compile, what stays refused, the C++ the design promises against today's, each open decision, where the designers disagreed, and the mechanism last. Opens with a summary and the numbered list of items, then one item per message on "next", each (but the final mechanism item) a self-contained probe file with CPython's result, today's C++ and the promised C++. Invoke when the user asks to "walk me through the design", "go over the plan one by one", or accepts the walk offered at the end of a localized or architectural design.
---

# /tpy-design-walk

An interactive design review, the design-time sibling of
`/tpy-review-walk`. Nothing is implemented yet: the walk shows what the
design PROMISES for someone using the compiler -- which programs will
compile, what they will do, what stays refused, the C++ they will get --
against what the compiler does today. The usual short design presentation
stays the default; this walk is offered at its end and is for discussing
the design point by point.

## Arguments

Optional unit name (the `/tmp/agents/<unit>/` directory). Defaults to the
unit whose design was presented last in this session.

## Step 1: Gather the design

Read `/tmp/agents/<unit>/design.md` (the design as presented), and when
the design came from "Independent designs" (`/tpy-add-feature`) its
`designers-<n>/brief.md` and `designers-<n>/<name>/design.md` (the latest pass `<n>`), with the merge table in `designers-<n>/merge.md`; the critics'
findings in `critics.md`; and the pending decisions. The walk presents this design; it does not
change it. A point the walk shows to be wrong (a promised render the
mechanism cannot give, a shape that already compiles) is reported to the
user as such, never silently corrected.

## Step 2: Group into items

One item per **user-visible promise**, the kinds:

- **Behavior change** -- code that compiles today and will behave differently (a result, a copy where there was a move, a new warning).
- **Newly supported** -- a shape refused or failing to build today that the design makes compile.
- **Stays refused / newly refused** -- the adjacent shapes the design leaves out or starts refusing, with the diagnostic.
- **C++ change** -- same behavior, different promised C++ (a copy / allocation / check removed or added, a different form).
- **Decision** -- an open choice, presented as CLAUDE.md "Decisions stand alone" asks: the Python shape, the C++ each option yields, labelled options with consequences, recommendation first, each cross-check reviewer's verdict last.
- **Designer disagreement** -- where the independent designers took different directions and how the merge resolved it (or that it is a decision item).
- **Mechanism** -- ALWAYS the last item: the fact and its home, the existing helpers reused, any special case added and why, the core data structures touched, the snapshot churn the plan expects (count, kind, one before/after line).

Keep it short -- usually under ten items: minor promises of one kind
share an item with one probe for the most representative shape. Order:
decisions and behavior changes first, then newly refused, newly
supported, C++ changes, disagreements, mechanism. Mark **[risk]** any
item that changes existing code's behavior, moves a reference type
across a boundary, adds a copy / allocation / runtime check, or diverges
from CPython.

## Step 3: Build every probe before the opening message

Every item's probe (all but the mechanism item) is written and run now, so "next" is instant: today's
C++ or diagnostic from the base sha, and the CPython run. Write each
item message in full to `/tmp/agents/<unit>/walk/p<k>/item.md`. A probe
that contradicts the design (the shape already compiles today, today's
C++ is not what the design says it replaces, CPython gives another
result) is reported in the opening message, not hidden.

## Step 4: Opening message -- summary + item list

```
# Design: <unit> -- <one-line gist>

Base: <sha> | scope: <localized | architectural> | confidence: <High | Medium | Low> -- <what caps it>

## Summary
<2-4 sentences, no bullets: the problem, the direction chosen and why, what will work that does not today, what it deliberately leaves out.>

- Designers: <who designed (architectural); where they agreed; each disagreement in one line, "-> item k">
- Critics: <who reviewed; what changed; what was rejected and why>
- Contradicted by a probe: <anything Step 3 found> (omit if none)

## Items (<N>)
1. [risk] <the promise, in user terms>
2. ...

Say next for item 1.        (or, when no decision is open and no probe contradicts the design: "or yes to approve the design as it stands")
```

## Step 5: One item per message

Wait for "next" (or an item number) before each, then show its prepared
`item.md`:

````
## Item <k>/<N>: <promise>  [risk]
```python
<the self-contained probe, 5-25 lines>
```
Probe:    /tmp/agents/<unit>/walk/p<k>/main.py
CPython:  <output, or the exception>
Today:    <1-6 lines of base C++, or "refused: <diagnostic>", or "build failed: <error>">
Promised: <1-6 lines of C++ the design states, or the diagnostic it keeps>
Rule:     <one sentence, in Python terms>
Why:      <one or two lines: why this shape works this way under the design>

Say next for item <k+1>.
````

A decision item uses the decision layout instead (shape, options with
their C++, recommendation, reviewer verdicts). The mechanism item shows
no probe: a few bullets naming the fact, its home and the data
structures, and whether it is an existing generic path or a special
case (why).

Probe rules (as `/tpy-review-walk`):

- Self-contained: every class, helper signature and import it uses; the message shows it whole at the printed absolute path.
- The first comment line states the claim; every subject line carries a trailing comment with what happens there today and under the design (`# today: refused "...", design: compiles, moves`).
- **Today** is copied from `uv run --project <base checkout> tpyc main.py -o out_base` (and `uv run --project <base checkout> tpy main.py` when the output or a build error matters); never inferred. **Promised** is the design's stated render -- marked as promised, never presented as built output. When a spike branch exists, copy it from the spike's build and say so.
- **CPython** from `uv run --project <base checkout> python main.py` (the probe directory is outside the checkout); a TPy-only surface says so.
- One probe build at a time.

## Questions between items

Answer each with a probe of the same layout (`p<k>-a/`, ...) and a
recommendation: keep the design, amend it (say how, and that the
critics re-check an amendment that changes the approach), or make it a
decision. Nothing is implemented during the walk. "yes" approves the
design only once every open decision is answered and every promise a
probe contradicted is corrected or withdrawn. An amendment agreed during
the walk is written into `design.md`, the items it changes are rebuilt
before they are shown, and it is restated in full before the approval
covers it.

## Rules

- Lead every item but the mechanism with Python and C++; the mechanism comes last, once.
- Never present promised C++ as built output.
- Keep each item to about one screen; the user asks for depth.
- After the last item, list the amendments and decisions taken, and ask for the approval.
