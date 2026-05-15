# Subprocess/Stdout Orchestration Anti-Pattern

Discovered during Prism analysis of a pipeline built on Hermes Agent
(May 2026). The system had 3 levels of subprocess chaining where data
flowed through stdout prefixes.

## The anti-pattern

```
enrich.py --text "..." --relations
  │ prints "KG: {...}" to stdout
  │ prints "RELATIONS: {...}" to stderr
  v
run.py (reads stdout, parses KG: prefix)
  │ prints "KANBAN: task created t_xxx" to stderr
  │ prints "MEMPALACE: {add_drawer}" to stdout
  v
api_bridge.py (reads stderr from run.py subprocess, parses KANBAN: prefix)
```

## Why it is dangerous

1. **Stringly-typed coupling** -- each layer depends on exact prefix strings.
   If enrich.py changes "KG:" to "KNOWLEDGE_GRAPH:", run.py breaks silently.

2. **No compiler/type checking** -- stdout is untyped text. A typo in a prefix
   string is a runtime error that only manifests when the downstream parser
   fails to match.

3. **Error transparency** -- stderr from a subprocess mixes actual errors with
   informational messages. The parent process has to guess which lines are
   errors and which are log output.

4. **Blocking** -- `subprocess.run()` in an async handler (api_bridge.py) blocks
   the event loop for the full duration of the subprocess. On 4GB VPS with
   concurrent requests, this causes stalls.

5. **No observability** -- if the subprocess hangs or crashes, the parent
   process only knows "returncode != 0". There is no way to inspect partial
   output, replay steps, or resume from checkpoint.

## How to detect

- `grep -r 'subprocess.run\|subprocess.Popen'` in your codebase
- Search for `startswith("PREFIX:")` patterns in stdout/stderr parsing
- Count how many layers deep the subprocess chain goes (3+ is a strong smell)

## What to use instead

| Instead of | Use |
|------------|-----|
| subprocess → stdout parsing | In-process function calls with typed return values |
| print("PREFIX: " + json) | Direct API call (e.g., `mempalace_kg_add(...)`) |
| stderr for info + errors | Structured logging (Python `logging` module) |
| shelling out to CLI | Gateway API (`/v1/chat/completions`, `/api/jobs`) |
| subprocess.run in async | `asyncio.create_subprocess_exec` or delegate to thread pool |

## Mitigation if you must use subprocess

- Use JSON-lines format (one JSON object per line) instead of string prefixes
- Separate stdout (data) from stderr (errors) strictly
- Add a timeout to every subprocess call
- Log the full stdout/stderr before parsing, so you can debug format changes
