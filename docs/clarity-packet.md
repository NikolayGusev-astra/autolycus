# Clarity Packet Plugin

SHA-256 dependency graph + stale document detection for `.clarity-protocol/` directories.

## What It Does

When a project has a `.clarity-protocol/` directory (created by `clarity-flow` skill), the plugin:

1. **Tracks document hashes** — SHA-256 of every `.md` file in `.clarity-protocol/`
2. **Detects stale documents** — When an upstream doc changes, marks downstream docs as stale based on dependency graph
3. **Injects warnings** — Pre-turn hook auto-injects STALENESS/DECISION warnings into agent context
4. **Tracks decisions** — Flags decisions for reconsideration when their related documents change

Zero overhead when `.clarity-protocol/` is absent (~1 file stat check per turn).

## Usage

### Automatic (via pre_turn hook)

The hook fires automatically when the agent's workdir contains `.clarity-protocol/`. No agent action needed.

### Manual tools

```
clarity_packet_report          — full staleness report
clarity_packet_record <path>   — record current hash (call after updating a doc)
```

### CLI

```bash
python -m plugins.clarity_packet.packet_status <project_dir> --report
python -m plugins.clarity_packet.packet_status <project_dir> --record goal/problem.md
```

## Document Model

```
.clarity-protocol/
├── config.json          ← SHA-256 hashes + dependency graph + decision state
├── summary.md           ← narrative overview
├── goal/                ← problem, stakeholders, requirements, open-questions
├── solution/            ← solution, architecture, summary
├── failures/            ← failure modes with chains and intervention points
├── decisions/           ← ADR records with reconsideration triggers
└── messaging/           ← audience-specific narratives
```

## Dependency Graph

```
problem → stakeholders → requirements → solution → architecture
                                         → failures ↔ architecture
                                         → decisions
```

When any node changes, all downstream nodes are marked stale.

## Integration with clarity-flow Skill

The `clarity-flow` process skill uses this plugin for automatic staleness tracking. After each document write, the agent calls `clarity_packet_record`. The plugin's pre-turn hook injects stale warnings at session start.

## Requirements

- Python 3.12+
- No external dependencies (stdlib only: hashlib, json, pathlib, dataclasses)

## Tests

```bash
python -m pytest tests/plugins/clarity_packet/ -v
```

58 tests covering: hash computation, config I/O, staleness detection, transitive chains, decision reconsideration, CLI, hooks, e2e lifecycle.
