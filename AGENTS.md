# Project instructions

- This repository contains the complete Paseo provider and native Hermes profile distribution under `hermes/`.
- Keep upstream frameworks unchanged; use native extension and lifecycle interfaces.
- Keep personal names, facts, host identifiers, credentials, and runtime data private.
- Documentation covers the current package and its use. Do not add investigation logs, test-run notes, implementation rationale, or roadmaps.
- Keep regression checks in `tests/`, outside runtime and distribution folders. Run `npm run typecheck`, `npm run check`, and relevant Python checks before publishing or applying changes.
- Use `uv run --isolated --locked --project hermes` for project Python commands. Keep dependency environments outside the native distribution.
- Own only individual shipped files and extension directories. Preserve local configuration, personal data, learned skills, schedules, and independent extensions.
- Never include assistant or author attribution in Git metadata.
