---
name: prep-merge
description: Squash current branch changes into a fresh branch ready to merge to master.
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
2. **Scan for suspicious files before squashing.** Whatever is in the diff vs master lands in `master` after the squash, so vet it -- but calibrate to this repo; don't nag about files that are normal here. Take the full changeset (`git diff master --numstat`, plus any uncommitted changes) and triage:
   - **Hard-stop (always warn, block until resolved):** secrets and local/editor config -- `.env*`, `*.local.json`, `settings.local.*`, `.vscode/`, `.idea/`, `.codex/`, `*.key`, `*.pem`, `*.crt`, `credentials*` -- and stray binaries *outside* known-legit locations (e.g. a `.wad`/`.so`/archive at the repo root).
   - **Expected here -- do NOT flag:** vendored third-party source and sidecars under `runtime/cpp/third_party/**`, generated snapshots under `tests/cases/*/expected/**` (these can legitimately be large), per-case `options.json` / `*.vendor.json`, and new project docs under `docs/**`.
   - **Borderline -- one-line note, don't block:** an unusually large *non-generated* addition, or a top-level scratch-looking `.md` / script. Mention it in passing so the user can veto; don't halt the flow.

   For hard-stop items only, **STOP and warn the user** with the list and why each is suspicious. Proceed only after they confirm the files are intended or tell you to exclude them. If excluding, do so on the working branch (`git rm --cached <file>` + add to `.gitignore`, or revert the add) before continuing -- never silently drop them.
3. If there are uncommitted changes, stage them on the current branch first (commit with message "dev").
4. Analyze the full diff vs master. Draft a commit message:
   - Summarize the nature of the changes (new feature, bug fix, refactor, etc.)
   - Keep the first line under 72 characters
   - Add a body explaining what and why
   - Do NOT include Co-Authored-By or references to Claude/LLM
5. Derive a branch name from the commit message (e.g. "sema: fix readonly deref" -> "fix-readonly-deref"). Keep it short.
6. Create the new branch from master, squash-merge the original branch, commit:
   ```bash
   git checkout -b <new-branch> master
   git merge --squash <original-branch>
   git commit -m "<message>"
   ```
   IMPORTANT: Always use `git merge --squash`. Never use `git checkout <branch> -- .` (it does not handle file deletions).
7. Show the final state: branch name, commit hash, diff stat.

## Important

- Do NOT push to remote
- Do NOT delete the original branch
- Do NOT pass `--date` to git commit -- let git use the system time automatically
