---
name: tpy-merge-master
description: Merge master into the current working branch and verify nothing broke. Captures what changed on both sides, performs the merge (resolving conflicts), then scrutinizes the merged result for semantic conflicts where master and the branch touched the same files or adjacent functionality. Ends with a short-bullet status summary. Invoke when you want to pull master into a feature branch safely.
---

# /tpy-merge-master

Merge `master` into the current working branch and confirm the merge didn't quietly break anything.

A clean textual merge is not a safe merge. When master and the branch edit the same file -- or adjacent functionality (a caller here, the callee on master; a renamed symbol; a changed signature, constant, or enum) -- git happily produces a result that compiles in its own head but is semantically wrong. This skill's real job is the *post-merge* check, not the `git merge` itself.

## Arguments

Optional source ref to merge in. Defaults to `master`.

- `/tpy-merge-master` -- merge local `master` into the current branch
- `/tpy-merge-master origin/master` -- merge the remote master (fetch first; see step 1)

## Context

Current branch: !`git branch --show-current`
Argument: $ARGUMENTS
Status: !`git status --short`
Merge base: !`git merge-base master HEAD 2>/dev/null || echo "(none)"`
Master commits since base: !`git log $(git merge-base master HEAD 2>/dev/null)..master --oneline 2>/dev/null || echo "(none)"`
Branch commits since base: !`git log $(git merge-base master HEAD 2>/dev/null)..HEAD --oneline 2>/dev/null || echo "(none)"`
Origin master vs local master: !`git rev-list --left-right --count master...origin/master 2>/dev/null || echo "(no remote)"`

## Steps

### 1. Pre-flight

- **Branch sanity.** If the current branch *is* `master`/`main`, abort -- there is nothing to merge into. This skill only runs on a feature/working branch.
- **Clean tree required.** If `git status --short` shows uncommitted changes, stop and report them. A merge needs a clean working tree; do not stash or commit on the user's behalf -- ask them to commit (auto-commit policy applies on a temporary working branch) or tell you how to proceed.
- **Resolve the source ref.** `$ARGUMENTS` if non-empty, else `master`. If it is `origin/master` (or the Context shows local master is behind origin), offer to `git fetch origin` first so the merge reflects real upstream state. Resolve the source to a stable SHA via `git rev-parse <ref>`.
- **Compute the merge base:** `git merge-base <source> HEAD`. If the base equals `<source>` (branch already contains all of master), report "already up to date" and stop. If the base equals `HEAD` (branch has no unique commits), the merge is a trivial fast-forward -- note it, perform it, and skip the interference analysis (there is no branch-side work to interfere).

### 2. Capture both sides (before merging)

This is what feeds the summary, so gather it before the tree changes:

- **What changed on the source side:** `git log <base>..<source> --oneline` and `git diff <base>..<source> --stat`.
- **What changed on the branch side:** `git log <base>..HEAD --oneline` and `git diff <base>..HEAD --stat`.
- **The interference set** -- the crux. Files touched by *both* sides:
  `comm -12 <(git diff <base>..<source> --name-only | sort) <(git diff <base>..HEAD --name-only | sort)`.
  Also note *adjacent* risk even when the file lists don't overlap: master changing a module/symbol that the branch's changed files import or call (and vice versa). A signature/rename/constant change on one side with call sites on the other is the classic silent breakage -- it won't appear as a file-overlap.

Read the actual diffs (not just stats) for the interference set and any adjacent-risk files, so you understand *both* intents going into the merge.

### 3. Perform the merge

Use `--no-commit --no-ff` so the result is staged and inspectable *before* it is committed:

```bash
git merge --no-commit --no-ff <source>
```

- **Clean merge:** the tree is staged, not committed. Proceed to verification (step 4) before committing.
- **Conflicts:** list every conflicted file. Resolve each one by understanding *both* sides' intent (from step 2), not by mechanically picking a side. For each manual resolution, record one line: file, what each side did, and how you reconciled them -- this populates the "needed manual merge" bullets. After resolving, `git add` the files. Do not complete the merge yet -- verify first.
- **Escape hatch:** if the merge looks too risky to resolve confidently, `git merge --abort` returns to the pre-merge state cleanly. Surface that option rather than guessing.

### 4. Verify nothing broke (the point of this skill)

A textually-merged tree can be semantically broken. Work the interference + adjacency set from step 2 against the *merged* result:

- **Re-read each interference site in the merged file.** Confirm both intents survive: the branch's change is still present and correct, master's change is still present and correct, and they compose. Watch for one side's edit silently swallowing the other's when they landed in the same hunk.
- **Cross-side semantic conflicts** (these merge cleanly but break):
  - A function/method renamed or re-signatured on one side, still called the old way from the other.
  - A constant / enum value / default argument changed on one side, relied on by the other.
  - A file/symbol *deleted* on one side and *modified* on the other.
  - Import or module-path changes on one side, referenced by the other.
  - A sema fact / AST field / codegen helper added on one side that the other side's new code should be using but isn't (this repo's tight coupling makes this common -- see CLAUDE.md "Bug-fix / Feature discipline").
- **Compile/test check.** This is a compiler; "nothing broke" means it still builds and tests pass.
  - First be CPU-aware: per CLAUDE.md, do **not** start a test run if one is already running (`pgrep -f pytest`).
  - Run a *targeted* subset over the affected areas first: `uv run pytest -k <pattern>` for the touched cases/categories, or rebuild a representative example if codegen was touched.
  - Recommend a final full `uv run pytest` before merge is considered done. Run it yourself only if the user asks, or auto-run on a temporary working branch if that matches how the session has been operating -- otherwise list it as an open item.
- **Hybrid depth.** Reason inline by default. If the interference set is large (more than ~8 files) or touches core sema/codegen structure, ALSO dispatch one fresh-context `general-purpose` Agent with the interference diffstat + both-side intent summaries (NOT the whole diff) to independently hunt for semantic conflicts you may have rationalized away. Fold its findings in. State the trigger in one line.

### 5. Finalize

- If verification is clean (or only low-risk notes remain), complete the merge commit: `git commit --no-edit` (keep git's default merge message). On a temporary working branch this matches the auto-commit policy; on any branch tracking a remote, confirm with the user first.
- If verification found a real breakage, do **not** commit a broken merge. Either fix it in place (then re-verify and commit) or `git merge --abort` and report what blocked it. Ask before abandoning if the fix is non-trivial.

### 6. Summary

Short bullets, omit empty sections. This is the deliverable:

```
# /tpy-merge-master: <MERGED CLEAN | MERGED WITH FIXES | CONFLICTS RESOLVED | ABORTED>

Merged <source>@<short-sha> into <branch>. <merge commit sha, or "not committed">

## Changed on master
- <bullet per theme of master's commits since base>

## Changed on this branch
- <bullet per theme of the branch's commits since base>

## Interfered
- <file/area touched by both sides, and how they related -- or "none">

## Needed manual merge
- <file: how the conflict was reconciled -- or "none, clean merge">

## Risks
- <semantic-conflict concern, untested area, or assumption that should be double-checked -- or "none identified">
```

Keep every bullet to one line. The Risks section is where you flag adjacency concerns you couldn't fully rule out and anything a human should eyeball -- be honest, don't pad, but don't hide a real "I'm not 100% sure these compose" either.

## Important

- This skill *brings master's commits into the branch* via a merge commit -- that is the requested action, not a policy violation. It does not push, rebase, force-push, or squash.
- Never resolve a conflict by blindly picking `--ours`/`--theirs` to make it go away. A wrong resolution is exactly the silent breakage this skill exists to prevent.
- Do not regenerate snapshots to paper over a merge: if `tests/cases/*/expected/**` differs after the merge, that is a signal to investigate, not to re-baseline. Re-baseline only after you understand *why* the generated code changed.
- `git merge --abort` is always the safe exit while the merge is uncommitted. Prefer it over committing a merge you don't trust.
