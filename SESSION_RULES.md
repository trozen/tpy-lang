# Session queue rules (`SESSION.md`)

An orchestrating session keeps the queue of work units for its worktree in
`SESSION.md` at the worktree root. The file is untracked (`.gitignore`), one
per worktree. It holds order and state; the record of each unit lives in
`TODO.md` / `BUGS.md`.

## Who
- The orchestrating session only. Subagents never read or write it, and
  briefs do not mention it.
- The user may edit it directly, and their edits win: re-read the file
  before every update and never overwrite their changes.
- After a compaction or in a new session: read this file and `SESSION.md`
  before anything else.

## Layout -- one screen
```
# <worktree> -- <workstream in one line>

## Current
- [<state>] <record link> -- <branch> @ <sha> (<worktree>)

## Next
<optional: one priority line, e.g. "this session pushes X; bug units do
not divert it" -- the user's ordering rule, dated; it orders the queue and
never widens or narrows an approved unit's scope>
1. [<state>] <record link> (<one-line note>)

## Waiting on other sessions
- <session/branch>: <what we wait for, or what we owe them>

## Done
- [merged] <sha> <subject> (<date>). Bulk-delete: <branches>
```
"Done" keeps the last few landings; "Added" (optional) lists work taken on
that was not in the plan, each with a one-line reason.

## Items
- High level only: one item = one future squashed commit on master.
- Each item links its record: a `TODO.md` entry title (quoted, greppable) or
  a `BUGS.md#<slug>`. The record lives there; the file adds order and state.
  A small process or docs unit (a skill, CLAUDE.md, tooling the user asked
  for) may name the user's ask with its date instead; a branch that is
  already one commit off master counts as squashed.
- No procedural steps (merge master, review rounds, worktree cleanup), no
  history: what the unit is and where it stands.
- Work not on the list: add it under "Added" with a one-line reason first,
  or stop -- otherwise it is drift. Work that completes the current unit
  (CLAUDE.md "A branch holds its goal") is part of that unit, not a new
  item.

## States
| State | Meaning |
|---|---|
| queued | listed; not approved yet (its analysis may be underway) |
| designed | analysis / design APPROVED by the user and a go given |
| implemented | code on the branch, its own tests green |
| reviewed | `/tpy-review` converged, fixes applied, full suite green |
| ready | `/tpy-merge-master` (if master moved) + `/tpy-ready` READY, squashed by `/prep-merge`; awaiting the user's merge |
| merged | on master (sha) |

- A presented but unapproved design stays `queued` with "decision pending".
- Annotations beside a state, not states: `blocked on <x>`,
  `decision pending: <what>`.
- From `implemented` on, the item names its branch and worktree
  (`<branch> @ <sha>`).
- A code change after `reviewed` or `ready` moves the item back to
  `implemented` until it is reviewed again; NOT READY never leaves an item
  at `ready`.

## Not authority
- A listed item is not a go. Each unit starts on the user's go; each merge
  to master needs its own go after the user has seen the summary.
- "Waiting on other sessions" lines are this session's view: check the
  other branch's real state before acting on one.

## When to update
Unit start, each state change, a user decision that changes the order, an
added item -- not continuously. In particular: the user's go on an approved
analysis or design (`designed`), `/tpy-ready` (its verdict), `/prep-merge`
(`ready`, the squash branch @ sha), the user reporting a merge (check that
master contains it, then `merged` with the sha under "Done").

`SESSION.md` is the authoritative queue of its worktree; a handoff memory
(`/tpy-handoff`) is a snapshot derived from it, never a second list.
