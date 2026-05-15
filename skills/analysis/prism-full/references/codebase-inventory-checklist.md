# Codebase Inventory Checklist

Use BEFORE starting analytical passes in Prism Full. Prevents analyzing architecture
as designed vs architecture as implemented.

## Step 1: Verify ASSERTIONS against CODE

For every factual claim in the artifact, ask:
- Does the code actually exist? (`ls`, `find`, `grep -l`)
- Does it work as described? (read the implementation, check error paths)
- Is it actually CALLED from the main loop? (grep for invocations)
- Is there a default no-op implementation that silently does nothing?

**Example (May 2026):** Artifact claimed `/kg` commands exist. Reality: no handler
in `commands.py`, only prompt instructions in AGENTS.md. Prompt fiction.

## Step 2: Map PLATFORM API

Find and read:
- Base classes and ABCs (`isinstance(attr, type) and issubclass(attr, BaseClass)`)
- Extension points: hooks, callbacks, abstract methods
- Return values: are they used or discarded?
- Config: does the current config activate the feature?

**Target files for Hermes Agent:**
- `agent/memory_provider.py` — MemoryProvider ABC, all hooks
- `agent/memory_manager.py` — MemoryManager delegation
- `agent/context_compressor.py` — compression pipeline
- `agent/context_engine.py` — ContextEngine base
- `run_agent.py` — main loop, hook invocation sites
- `tools/memory_tool.py` — MemoryStore, MEMORY.md format
- `plugins/memory/__init__.py` — provider loading
- `plugins/memory/<name>/` — bundled providers as examples
- `hermes_cli/commands.py` — slash command registry

## Step 3: Run VERIFICATION

- Count files and lines: `wc -l`, `find ... | wc -l`
- Check timestamps: `stat`, `ls -lt`
- Run a test: create data, verify persistence
- Check logs: grep for WARNING/ERROR related to component

## Step 4: DOCUMENT discrepancies

Table format:
```
| Компонент | Как описано | Как работает | Расхождение |
```
