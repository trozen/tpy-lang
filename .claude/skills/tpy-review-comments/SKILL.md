---
name: tpy-review-comments
description: Handle the user's line-level review comments on a branch (a gitr export -- "Review comments: N items", `file:line` + diff line + comment -- or lines marked `!!` / `>>` / `??`). Answers each item first, classifies it (fix now / file / answer only / decision), fixes the clear in-branch defects, files the rest, and waits on questions and design remarks. Invoke whenever the user pastes review comments.
---

# /tpy-review-comments

The user reviews generated C++ and test cases and pastes comments. Each comment is either a question, a defect report, or a design remark -- and a question is never an approval to change the branch.

## Input

`$ARGUMENTS` or pasted text from the user's review tool, gitr (`~/dev/gitr`; `gitr --export <base>` prints the same export). The export looks like:

`````
Review comments: N items (note, bad)

## path/to/file

- path/to/file:LINE (+) [note]
  ```diff
  <the diff line>
  ```
  <comment text>
`````

The `[kind]` flag carries the intent: `bad` -- the user thinks the line is wrong; `note` -- a question or remark; `good` -- approval (not exported by default). A comment copied from the review window or typed by hand instead starts with a marker: `!!` (bad), `>>` (note), `??` (a question), `++` (good). Plain prose comments are accepted too.

## Steps

1. **Read every item against the tree.** Open the file at the line; for a snapshot hunk also open the case's `src/main.py` and, when the question is "why does it render like this", find the lowering/emit site. Answer from the code, not from memory; probe with `uv run tpy` when behavior is in doubt.
2. **Classify each item:**
   - **Answer only** -- a question whose answer requires no change ("what is X?", "why?"). If the honest answer is "it shouldn't", reclassify.
   - **Fix now** -- a defect introduced or exposed by this branch, in its scope, with an obvious fix (wrong test note, missing annotation, broken test, a render this branch made worse).
   - **File** -- a real issue outside this branch's goal or pre-existing: defect -> `BUGS.md` with a slug; gap/idea/optimization -> `TODO.md`. A test that pins the defect cites `BUGS.md#<slug>`.
   - **Decision** -- the comment questions a design or language choice ("shouldn't this be a warning?", "should X and Y share a path?"). Never applied to the branch on the strength of the comment.
3. **Respond first**, one numbered block per item, in the user's order:
   `N. <file:line> -- <Answer | Fix now | File -> slug | Decision>` then 1-4 lines. Explanations of behavior use a short Python snippet and the generated C++ line, not compiler internals.
4. **Act by default** on Fix now and File items right after responding. If the user wrote "respond first" / "discuss" / "don't change yet", stop after step 3 and wait.
5. **Decisions**: after the response, present them one at a time (Python shape, C++ per option, labelled options with consequences, recommendation first) and wait for each answer. A design idea raised while reviewing a finished branch defaults to a TODO.md followup, not to this branch.
6. **Verify and commit.** A targeted test run over what changed (CLAUDE.md "Agent testing workflow"); regenerated snapshots follow CLAUDE.md's snapshot policy. Commit the round as one commit on the working branch. If the user is reviewing a squash branch, commit on top of it -- never re-squash; amend only when asked.
7. **Close** with one line per bucket: fixed (items), filed (slugs), answered (items), awaiting you (decisions).

## Rules

- Scope follows CLAUDE.md "A branch holds its goal": the user is reviewing, so additions other than fixes to what the branch introduced become followups.
- Never parse a comment as consent to a neighboring change it did not mention.
- If several comments point at the same root cause, say so once and treat them as one item.
