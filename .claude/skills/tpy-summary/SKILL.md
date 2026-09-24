---
name: tpy-summary
description: Summarize the current branch for the user's review -- what works now with Python examples, the generated C++ before/after, what stays rejected, snapshot churn binned, and where to look first. Invoke when the user asks to "summarize the branch / changes", "what does this branch contain", or "what should I review".
---

# /tpy-summary

A branch summary the user reviews from. They review two things: whether the generated C++ is correct and whether the language behavior makes sense -- so the summary is written in those terms, with the compiler mechanism last.

## Arguments

Optional base ref. Defaults to the merge-base with `master`.

## Context

Current branch: !`git branch --show-current`
Commits vs master: !`git log master..HEAD --oneline 2>/dev/null | head -30 || echo "(none)"`
Master commits not on branch: !`git log HEAD..master --oneline 2>/dev/null | head -10 || echo "(none)"`
Diff stat vs master: !`git diff $(git merge-base master HEAD) --stat 2>/dev/null | tail -1`

## Steps

1. Resolve the base (`$ARGUMENTS`, else `git merge-base master HEAD`) and read the diff: sources, new/changed test cases, and the snapshot diffs under `tests/cases/**/expected/` and `tests/interop/**/expected/`.
2. Group the changes by **user-visible effect**, not by file or commit.
3. Write the summary in the format below. Every C++ line shown is copied from a real snapshot or `--dump-code` output, never paraphrased or invented; every Python snippet is (or is cut down from) a real test case -- name it.

```
# <branch>: <one-line gist>

<master: up to date | N commits behind> | reviewed up to <sha, if known> (<N> commits unreviewed) | suite green @ <sha> | <diffstat one-liner>

## What works now
### <effect 1>
<3-8 line Python snippet>            (case: tests/cases/<group>/<case>)
-> <behavior: output / accepted / diagnostic text>
C++ before:  <the 1-4 lines that changed, or "rejected: <error>">
C++ after:   <the 1-4 lines>
### <effect 2> ...

## Still rejected / limitations
- <everyday shape> -> <diagnostic> (BUGS.md#<slug>)      (only everyday shapes; exotic ones as a count)

## Snapshot churn on existing cases -- <N> cases
- improvement (<n>): <one-line kind> e.g. <case>: `<before>` -> `<after>`
- neutral (<n>): ...
- regression (<n>): ...                                   (must be empty or explained)

## Compiler changes
- <2-5 bullets, mechanism level: what path now handles what, and whether it is the shared generic path or a special case (hard-coded name, per-construct branch) -- say why for any special case; net lines in tpyc/ (+a/-b)>

## Review first
1. <file or case> -- <why: behavior change / reference-type boundary / new runtime helper>
2. ...

## Open
- <decisions pending or followups filed, with slugs>        (omit if none)
```

## Rules

- Lead with behavior and C++; no history of how the branch got here, no review-round narrative.
- "Review first" orders by risk to correctness: generated-C++ changes that alter behavior on existing cases, then new cases moving a reference type across a boundary (aliasing vs copy), then runtime headers, then compiler code. Cap it at ~6 entries.
- Mention any divergence from CPython the branch introduces or keeps, with its warning and escape hatch.
- Keep it to about one screen; the user asks for depth on any item.
