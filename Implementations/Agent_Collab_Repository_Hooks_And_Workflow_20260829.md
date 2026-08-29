# Agent Collab Repository Hooks and Workflow

## Goal

Make the supplied Agent Collab Git protections active for `ODIN-Modern` only.
Do not change Git configuration for other repositories or for the user account.

## Current state

- The hook scripts are under `githooks/`, while their own setup instructions
  require `.githooks/`.
- The GitHub Actions file is under `github-workflows/`, which GitHub does not
  load as a workflow directory.
- Neither a repository-local nor global `core.hooksPath` is configured.
- `.gitignore` was narrowed to ignore transient Agent Collab claim and lock
  state, but mutable reader cursors and session heartbeats also require
  exclusion.

## Implementation

1. Move the two hook scripts to `.githooks/` without changing their behavior.
2. Move the protection workflow to
   `.github/workflows/protect-coordination-records.yml`.
3. Ignore all five mutable state directories: `claims`, `locks`, `readers`,
   `sessions`, and `pin_acknowledgements`. Retain durable coordination records.
4. Force LF line endings for `.githooks/*` so Windows checkout conversion cannot
   break the shell scripts.
5. Commit the repository files as a separate infrastructure commit.
6. Set `core.hooksPath` with `git config --local` so only this clone of
   `ODIN-Modern` uses the hooks.

## Safety boundaries

- Do not set or alter global Git configuration.
- Do not stage or bulk-commit `.agents/` as part of this setup change.
- Do not push, rewrite history, or change branch protection settings.
- Keep ODIN application code out of the infrastructure commit.

## Validation

- Confirm the old loose directories are gone and the standard paths exist.
- Run shell syntax checks on both hooks.
- Confirm the workflow is valid YAML when a local parser is available.
- Run the pre-commit hook with only the infrastructure paths staged.
- Confirm `git config --local --get core.hooksPath` returns `.githooks` and the
  global value remains unchanged.

## Validation results

- Both hook scripts passed `sh -n` under Git for Windows.
- The workflow passed a local YAML parse.
- All five mutable Agent Collab directories resolved as ignored; a checkpoint
  fixture remained trackable.
- An alternate-index negative test force-staged a session record and proved the
  pre-commit hook rejects transient state with exit code 1.
- The normal pre-commit path passed with only the six infrastructure files
  staged.
- `.githooks/*` resolves to `eol=lf`, and `core.hooksPath` resolves from this
  repository's `.git/config`. The global hooks path remains unset.

## Status

Implementation and local validation complete on 2026-08-29. The user explicitly
approved the sixth `.gitattributes` path and requested a complete transient-state
ignore review. That review confirmed three exclusions were missing in addition
to the original two.
