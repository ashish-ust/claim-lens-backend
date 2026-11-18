---
name: commit
description: Commit all pending changes in this repo as one commit with a single-line message that explains what changed. Use when the user asks to commit, or to save the current changes to git.
---

# Commit changes

Make exactly one commit that holds all pending changes, with a one-line message.

## Steps

1. Check the repo state:
   - `git status --short` to list changes.
   - `git diff --stat` and `git diff --cached --stat` for the size of the change.
   - If this is not a git repo, stop and tell the user. Do not run `git init` unless they ask.
2. Check for files that must not be committed. Stop and report if any of these show up as changes or untracked:
   - `.env` or any file with secrets or an API key
   - `data/` (databases, uploaded claim photos)
   - real images under `sample_images/` (only the README belongs there)
   - `.venv/`, `__pycache__/`, `.pytest_cache/`
3. Read the diff (`git diff` and the untracked files) so the message describes what actually changed, not just file names.
4. Stage everything with `git add -A`, then confirm with `git diff --cached --stat`.
5. Write one line that says what changed, in the imperative mood and under 72 characters. Examples:
   - `Add duplicate-check thresholds and reference seeding`
   - `Fix 422 error shape for nested validation errors`
6. Commit with that line as the subject only. Do not add a `Co-Authored-By` trailer or any other attribution line:

   ```
   git commit -m "<subject line>"
   ```

7. Run `git log -1 --stat` and show the user the commit hash, subject and file count.

## Rules

- One commit per run. Do not split the changes into several commits unless the user asks.
- Do not push, amend, rebase or force anything.
- Do not use `--no-verify` or disable signing. If a hook fails, report the output and stop.
- If the user gives a message, use theirs as long as it is one line.
