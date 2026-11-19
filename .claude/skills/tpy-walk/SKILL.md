---
name: tpy-walk
description: Walk the user through a branch for review, one change at a time -- what changes for a compiler user (language rules, what is newly supported, what is refused, how the generated C++ changes), taken from the branch's new and changed tests and every snapshot it churns. Opens with a summary and the numbered list of items, then one item per message on "next", each a self-contained probe file with CPython's result and the C++ before (base) and after (branch). Invoke when the user asks to "walk me through the branch", "go over the changes one by one", or "list the changes with examples".
---

# /tpy-walk

An interactive branch review. The user reviews what changes for someone
**using** the compiler: which programs compile, what they do, what is
refused and with which diagnostic, and the C++ they get. The tests are
the record of exactly that, so the walk is built from them: every new
test case, every changed test source / annotation / `output.txt`, and
every changed snapshot is part of some item. Compiler internals appear
only as a one-line note under an item and in the opening summary.

## Arguments

Optional base ref. Defaults to the merge-base with `master`.

## Context

Current branch: !`git branch --show-current`
Commits vs master: !`git log master..HEAD --oneline 2>/dev/null | head -30 || echo "(none)"`
Master commits not on branch: !`git log HEAD..master --oneline 2>/dev/null | head -10 || echo "(none)"`
Diff stat vs master: !`git diff $(git merge-base master HEAD) --stat 2>/dev/null | tail -1`

## Step 1: Inventory the test-visible changes

Resolve the base (`$ARGUMENTS`, else `git merge-base master HEAD`) and
list, from `git diff <base> --name-status`:

- **New cases** under `tests/cases/` and `tests/interop/` (`src/` and `expected/`).
- **Changed existing cases**: `src/*.py` (incl. `# tpyc:` annotations), `output.txt` / `panic.txt`, a renamed `error_` / `panic_` status, `no_cpython.txt`, `options.json`.
- **Snapshot churn**: every changed `expected/**` file -- generated `.hpp` / `.cpp`, `diag.txt`, `mir.txt` -- including cases whose sources did not change.
- **C++ runtime**: changed files under `runtime/cpp/` (headers, traits, helpers, vendored-library facades).
- **User-facing docs and stubs**: `docs/LANGUAGE_FEATURES.md`, `docs/STDLIB_ROADMAP.md`, stubs under `lib/tpy/` -- they name the rule a test pins.

Every path in the first four groups must be accounted for (Step 2) --
in an item of its own, a folded item, or a one-line mention; nothing
test-visible is silently left out. For a large branch, an Explore
agent may produce the inventory (paths + one line each); grouping,
probes and items stay with you.

## Step 2: Group into items

One item per **user-visible effect**, not per file or commit. The kinds:

- **Language rule** -- a behavior that changes for code that compiled before (a different result, a copy where there was a move, a new warning).
- **Newly supported** -- a shape that was refused (or failed to build) on the base and now compiles.
- **Newly refused / diagnostic change** -- a shape that compiled and is now an error, or a diagnostic whose text, position or level changed.
- **Generated C++ change** -- same behavior, different C++ (a copy / allocation / check removed or added, a different form).
- **Runtime change** -- a new or changed runtime helper, trait or type (what the generated C++ calls, how a value is passed or compared, a check it performs). Shown with the changed runtime lines (a few, copied from the diff) and a probe whose generated C++ reaches them; a runtime change no generated code reaches yet is one line in "Also changed".
- **Stdlib effect** -- a stub or library change visible from user code.

**Keep the walk short -- use judgement.** A change gets an item of its
own only when it earns one: it changes what programs do or whether
they compile, carries a [risk], or states a rule the user would want
to see on its own. Everything else is folded or just mentioned:

- Minor changes of one kind (a few similar newly-supported shapes, small diagnostic rewordings) share one item, with one probe for the most representative shape and the rest listed.
- Cosmetic C++ churn (renames, reordering, equivalent spellings, no added or removed copy / allocation / check) is ONE item for the whole branch: a bullet per kind with its count and one before/after line, no probe per kind.
- A change too small for an item (a test comment, a fixture reshuffle) is one line in the summary's "Also changed".

Aim for a walk the user finishes in one sitting -- usually under ten
items. When in doubt whether something is cosmetic, it is not: give it
an item.

**Snapshot churn is fully accounted for.** Churn caused by a change an
item already walks goes in that item, which lists every affected
existing case. Churn no walked change explains is either the cosmetic
item above or, when it changes cost or behavior, an item of its own
per kind ("`const&` -> by value for small ValueType params, 14
cases"): one probe, the before/after C++, why it is equivalent or
better, and the full list of affected cases.

**Flag what the user did not decide in the session.** Mark **[flag]**
anything that is open to argument and was settled without the user in
the session that built the branch -- what the user chose or approved
in that conversation is not flagged. The session tells the two apart
from what it knows was decided, including what it recorded in its own
`SESSION.md` (CLAUDE.md "Orchestration"). Flag: a choice made
autonomously inside approved work (CLAUDE.md "Decide what the user
would decide"), a reviewer, critic or xcheck finding that was rejected
or where they disagreed, a plan deviation, a restriction or refusal
added as a stopgap, a special case kept. Find them in the
conversation, the branch's commit messages, the unit's `design.md` "Critics"
bullet and the review / decision reports under `/tmp/agents/<unit>/`.
A flag that changes what the user sees (behavior, acceptance, a
diagnostic, the C++) is shown in its item with what the other option
would have given -- its own item when no other item carries it. A flag
with no user-visible effect is one line in the summary's "Flagged".

Order items by importance for a user: [flag] items and behavior
changes of existing programs first, then newly refused, newly
supported, C++ changes, runtime, stdlib. Mark **[risk]** any item that changes behavior of existing
code, moves a reference type across a boundary (alias vs copy), adds a
copy / allocation / runtime check, or diverges from CPython; a churn
kind with any of these is ordered as a behavior change.

## Step 3: Opening message -- summary + item list

```
# <branch>: <one-line gist>

Branch: <branch> @ <sha> (<worktree path>) | <master: up to date | N commits behind> | reviewed up to <sha, if known> (<N> commits unreviewed) | suite green @ <sha> | <diffstat one-liner>

## Summary
- Flagged: <each [flag]: what was decided, by whom (autonomous / reviewer X vs Y), the alternative, its effect on the compiler or on user code; "-> item k" when walked> (omit if none)
- Compiler: <2-5 bullets, mechanism level: what path now handles what; shared generic path or special case (why); net lines in tpyc/ (+a/-b)>
- CPython divergence introduced or kept: <with its warning and escape hatch, or "none">
- Followups filed: <BUGS.md#<slug> / TODO.md "<phrase>" -- one line each> (omit if none)
- Open: <decisions pending, with the option recommended> (omit if none)
- Also changed: <test-visible changes too small for an item, one line each> (omit if none)
- No test-visible effect: <internal refactors, one line each; not walked> (omit if none)

## Items (<N>)
1. [risk] <the effect, in user terms>        (<new/changed cases>; churn: <n> cases)
2. ...

Say next for item 1.
```

## Step 4: One item per message

Wait for "next" (or an item number) before each. An item message:

````
## Item <k>/<N>: <effect>  [risk] [flag]
```python
<the self-contained probe, 5-25 lines>
```
Probe: /tmp/agents/walk/<branch>/p<k>/main.py        (shape from: tests/cases/<group>/<case>/src/<file>.py:<line>, one per subject line)
CPython:  <output, or the exception>
Before:   <the 1-6 lines of base C++, or "refused: <diagnostic>", or "build failed: <error>">
After:    <the 1-6 lines of branch C++, or the new diagnostic>
Rule:     <one sentence, in Python terms>
Runtime:  <runtime/cpp/<file>:<line>: the few changed lines>        (omit unless a runtime change)
Flag:     <what was decided and by whom; the alternative and what it would give for this probe>        (omit unless [flag])
Churn:    <n> existing cases: <case names, all of them>        (omit if none)
Mechanism: <one line: the compiler path, generic or special case>

Say next for item <k+1>.
````

**The probe is self-contained.** It defines everything it uses -- the
classes, the helpers with their full signatures (`def mk() -> Own[P]:`),
the imports -- so the reader never guesses a type. It is the file at the
printed absolute path, which the user can open and run; the message
shows it whole.

**The probe reads on its own.** The user must see what it shows
without first reading the rest of the item:

- Its first comment line states the claim in Python terms: what the branch changes for this shape ("A comprehension passed to a borrowed parameter still gets no MIR warning; its list-literal twin does").
- Every subject line carries a trailing comment with what happens THERE, before and after when they differ: `# before: copy, after: move`, `# warning: "..."`, `# refused: "..."`, `# no warning (not analyzed)`. A comparison twin says so: `# twin: same shape as a literal -> warned`.
- Helpers and scaffolding carry no comment unless a property of theirs is the point (`# mutates its argument`). Keep scaffolding to what the claim needs: one contrast pair per point, not every variant the case covers.
- `main()` prints are prefixed with the section name, so the output says which line produced what.

Evidence rules, each one checked, never assumed:

- Probe layout: `/tmp/agents/walk/<branch>/p<k>/main.py`, with `out_base/` and `out_branch/` (`tpyc -o`) beside it. One probe build at a time.
- **After** is copied from `out_branch/`; check it matches what the case's snapshot pins for the same shape, so the example and the test cannot drift.
- **Before** is copied from `out_base/`, never inferred from the snapshot diff. Probe from a checkout at the base: the main checkout when its `HEAD` is the base (the usual case after `/tpy-merge-master`), else a temporary `git worktree add <path> <base>` removed after the walk. Commands: `uv run --project <checkout> tpyc main.py -o out_<base|branch>`, plus `uv run --project <checkout> tpy main.py` when a build error or the output matters.
- **CPython** comes from `uv run python main.py` (the editable install puts `lib/cpy` on the path). For a TPy-only surface say "TPy-only".

## Questions between items

The user's questions go in between items. Answer each with a probe of
the same layout (`p<k>-a/`, `p<k>-b/`, ... -- self-contained, path
printed), and end the answer with a recommendation: nothing to do,
file it (BUGS.md / TODO.md), or fix it on the branch. Code changes
follow CLAUDE.md as everywhere: a fix only on the user's go, a decision
presented on its own. The walk resumes on "next".

## Rules

- Lead every item with Python and C++; no history of how the branch got here, no review-round narrative.
- Every C++ line is copied from a probe's output; every probe names the case line each subject line comes from (`src/<file>.py:<line>`), so the user can jump to what the test pins.
- Keep each item to about one screen; the user asks for depth.
- After the last item, say so and list what the walk produced (fixes made, entries filed, decisions taken).
