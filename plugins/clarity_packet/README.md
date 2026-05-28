# Clarity Packet Plugin

**SHA-256 dependency graph + stale document detection for `.clarity-protocol/` directories.**

Part of the Clarity Agent → OWL integration. See [clarity-flow skill](../skills/clarity-flow/SKILL.md) for the process framework.

## What It Does

When a project has `.clarity-protocol/` (created by `clarity-flow`):

1. **Tracks document hashes** — SHA-256 of every `.md` file
2. **Detects stale documents** — When upstream doc changes, marks downstream as stale via dependency graph
3. **Injects warnings** — Pre-turn hook auto-injects `STALENESS`/`DECISION` warnings into agent context
4. **Tracks decisions** — Flags decisions for reconsideration when related docs change

**Zero overhead** when `.clarity-protocol/` absent (~1 file stat check per turn).

## Files

```
plugins/clarity_packet/
├── plugin.yaml              ← Plugin manifest (auto-discovered by hermes_cli)
├── __init__.py              ← register(ctx): pre_llm_call hook + 2 tools
└── packet_status.py         ← Core: SHA-256, graph walk, stale detection

tests/plugins/clarity_packet/
├── test_packet_status.py    ← 35 unit tests
├── test_plugin.py           ← 15 hook + tool tests
└── test_e2e.py              ← 8 lifecycle tests
```

## Tools

| Tool | Description |
|---|---|
| `clarity_packet_report` | Full staleness report |
| `clarity_packet_record <path>` | Record current hash (call after doc update) |

## CLI

```bash
python -m plugins.clarity_packet.packet_status <project_dir> --report
python -m plugins.clarity_packet.packet_status <project_dir> --record goal/problem.md
```

## Dependency Graph

```
problem → stakeholders → requirements → solution → architecture
                                         → failures ↔ architecture
                                         → decisions
```

Data-driven: edit `DEFAULT_DEPENDENCY_GRAPH` in `packet_status.py` or provide custom graph in `config.json`.

## Tests

```bash
python -m pytest tests/plugins/clarity_packet/ -v
# 58 tests: hash, config, staleness, chains, decisions, CLI, hooks, e2e
```

## Integration

- `clarity-flow` skill: process routing, calls `clarity_packet_record` after each doc write
- `clarity-thinker-*` skills: 6 specialist lenses for failure analysis
- `pre_llm_call` hook: auto-injects stale warnings at session start
