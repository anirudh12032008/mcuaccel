# Blockers

Something that failed three times, what was tried, and the narrower fallback taken.

- 2026-10-10 `git init` / `.git` writes fail inside the Bash sandbox ("Operation not permitted").
  Workaround: the orchestrator runs git write commands outside the sandbox; agents never commit.
