# Test Plan Analysis — Autolycus Case Study (May 2026)

## Context

Autolycus project: unified fork of NousResearch/hermes-agent with 10 custom components
(P0-P6, ultra-governance, SBL, Sanitize, ClickHouse, findings_to_wiki).

## What happened

Initial test plan claimed:
- 21 tests needed (all components)
- ClickHouse = 🟢 (low priority)
- ContextWriter = unit-testable
- All components exist as standalone files

## What Prism adversarial found

| Claim | Reality |
|-------|---------|
| ContextWriter = unit-testable | ❌ It's a patch to `run_agent.py` line 14292, not a separate file. Needs integration test or E2E. |
| malloc_trim = unit-testable | ❌ Patch to `model_tools.py:handle_function_call()`. Same problem. |
| Unload after use = 1 test | ❌ Need 2: verify load AND verify unload |
| ClickHouse = 🟢 | ❌ Enterprise-critical multi-tenant storage. Silent failure = no enterprise demo. Must be 🔴. |
| SBL has 1 test | ❌ The "test" is a manual prototype script with no asserts — doesn't protect against regressions. |

## Corrected plan

- Unit tests for: Tool Policy, RTK Filter, SBL classification (standalone plugins)
- Integration tests for: ContextWriter, malloc_trim (core code patches → need E2E)
- Enterprise-critical: ClickHouse = 🔴 (not 🟢)
- SBL prototype → real pytest tests (106 written)

## Pitfalls

1. **Assume files = separate modules.** A commit message saying "add ContextWriter" may mean a 3-line patch to `run_agent.py`, not a new file. Check `git show --stat`.
2. **Assume test count = coverage.** 1 prototype file = 0 real tests. Read the file.
3. **Assume priorities based on complexity, not impact.** ClickHouse is simple code but enterprise-critical. SBL is complex but production-critical. Priority = impact × fragility, not LOC.
4. **Assume test env location.** Always ask: "Where does the victim/test container run?" before planning anything. If you don't ask, you'll SSH to remote servers while it's sitting on the user's laptop.
