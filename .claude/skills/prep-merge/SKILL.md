---
name: prep-merge
description: Squash current branch changes into a fresh branch ready to merge to master.
disable-model-invocation: true
---

# Prepare branch for merge

Squash all changes from the current branch into a single clean commit on a new branch, ready to merge to master.

## Context

Current branch: !`git branch --show-current`
Commits on branch vs master: !`git log master..HEAD --oneline 2>/dev/null || echo "(none)"`
Uncommitted changes: !`git diff --stat HEAD 2>/dev/null || echo "(none)"`
Full diff vs master: !`git diff master --stat 2>/dev/null || echo "(none)"`

## Steps

1. Verify there are changes vs master. If not, abort.
2. If there are uncommitted changes, stage them on the current branch first (commit with message "dev").
3. Analyze the full diff vs master. Draft a commit message:
   - Summarize the nature of the changes (new feature, bug fix, refactor, etc.)
   - Keep the first line under 72 characters
   - Add a body explaining what and why
   - Do NOT include Co-Authored-By or references to Claude/LLM
4. Derive a branch name from the commit message (e.g. "sema: fix readonly deref" -> "fix-readonly-deref"). Keep it short.
5. Create the new branch from master, squash-merge the original branch, commit:
   ```bash
   git checkout -b <new-branch> origin/master
   git merge --squash <original-branch>
   git commit -m "<message>"
   ```
   IMPORTANT: Always use `git merge --squash`. Never use `git checkout <branch> -- .` (it does not handle file deletions).
6. Show the final state: branch name, commit hash, diff stat.

## Important

- Do NOT push to remote
- Do NOT delete the original branch
- Do NOT pass `--date` to git commit -- let git use the system time automatically
