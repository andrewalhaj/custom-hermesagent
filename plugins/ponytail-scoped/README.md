# ponytail-scoped

Wraps the vendored Ponytail plugin (`~/.hermes/vendor/ponytail`, unmodified).

- **What:** registers everything Ponytail does, but replaces its `pre_llm_call` hook. Ponytail's rules are
  injected only after a coding signal (code-file edits, test/git/build commands, or a code-shaped user message),
  prefixed with `SCOPE.md` so non-code replies keep a normal voice.
- **Tune:** signal patterns are constants at the top of `__init__.py`; scope text is `SCOPE.md`.
- **Test:** `python3 -I -m unittest discover -s tests`
- **Enable:** add `ponytail-scoped` to `plugins.enabled` in config.yaml (and drop stock `ponytail` — both would inject), restart the gateway.
- **Rollback:** remove `ponytail-scoped` from `plugins.enabled`, restore `ponytail`, restart the gateway.
