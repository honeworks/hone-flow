---
name: deprecate-and-migrate
description: Change or remove public API, CLI flags or the run-folder format without breaking users - deprecation warnings, a compatibility period, format versions and migration notes. Use when a change renames, removes or changes the meaning of anything users or old run folders depend on.
---

# Deprecate and migrate

Users and their run folders outlive any version. Break nothing silently.

**Public API and CLI**
1. Keep the old name working for at least one minor release: it calls the new one and warns with
   `warnings.warn("<old> is deprecated, use <new>; it will be removed in <version>", DeprecationWarning, stacklevel=2)`.
   CLI: the same message on stderr.
2. Docs and examples show only the new form. `CHANGELOG.md`: **Deprecated** now, **Removed** when it goes.
3. Tests: the old form still works and warns; the new form works.

**Run-folder format** (`docs/run-format.md`, `design/current.md` §4.15)
1. Adding an optional key keeps `format_version`. Removing a key or changing its meaning needs a new
   version and a change record (`plan-change`) with a "Migration and compatibility" section.
2. Readers accept the previous version, or refuse it with a `HoneFlowError` that says how to migrate.
   Never guess.
3. Keep a small sample run folder of the old version in the tests and test both reading it and the
   refusal message.
4. Document the version history in `docs/run-format.md`.
