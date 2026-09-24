---
name: tpy-handoff
description: Close a unit of work or a session -- make sure every followup is tracked in the repo (TODO.md / BUGS.md), write the memory pointer for the next session, recommend continuing here vs a fresh session, and print a one-sentence bootstrap. Invoke when the user asks to "save followups", "fresh session or here?", "give me a bootstrap", or after a merge.
---

# /tpy-handoff

The next session starts from the repo and one memory pointer, not from this conversation. Several sessions run in parallel worktrees and write the same memory directory -- edit surgically.

## Steps

1. **Collect followups** from this session: review "file and defer" items, `/tpy-ready` followups and retrospective, items the user said to "file", open decisions, and the next steps of the current workstream. Drop anything already done.
2. **Track them in the repo.** Every durable followup must exist in `TODO.md` (gap / feature / cleanup) or `BUGS.md` (defect, with a slug). Add the missing ones in each file's existing structure; a memory note is a pointer, never the only record. Commit on the branch that will be merged (the squash branch if `/prep-merge` already ran; branch-aware policy). If everything is merged, make the edits on a fresh short branch `followups-<topic>` off master, never on master, and list that branch under branches in flight as needing the user's merge.
3. **Memory pointer.** Write or update ONE project memory for this workstream (`project_followups_<topic>.md`): the ordered followup list (one line each, with its `TODO.md` anchor phrase or a `BUGS.md` slug), branches in flight (name, sha, state: unmerged / squashed awaiting merge / merged), and pending user decisions. Replace the workstream's previous followups memory rather than adding another; update its single line in `MEMORY.md` with a targeted edit, leaving other sessions' lines alone.
4. **Here or fresh?** Recommend one in a line: continue here when the next item is in the same area and context is not heavy; a fresh session when the unit is done and the next item is a different area, or the context is heavy (then `/compact` is the alternative only if the next item depends on this conversation).
5. **Print:**

```
Followups (ranked):
1. <item> -- <why first> (<anchor/slug>)
2. ...
Tracked: <n> in TODO.md, <n> in BUGS.md (<new ones listed>) | memory: <file>
Recommendation: <here | fresh session> -- <reason>
Bootstrap: <one sentence>
```

The bootstrap is one sentence the user pastes into a new session: which memory to read, the precondition to check (e.g. "if master contains '<commit subject>'"), and the item to start with and how (`/tpy-fix-bug <slug>` / `/tpy-add-feature <item>`, fresh branch off master, which worktree).

## Rules

- "What's next?" asks for this ranked list; it is not a signal to start item 1.
- Unfinished structural items (unifications, collapsing parallel paths) rank above row-by-row fixes.
- No history in the followup text: say what should be done, not how the session got there.
