# System Boundary Layer — Agent Safety Architecture

**Date:** 2026-05-09
**Session:** 9-round Prism-full analysis of Hermes agent safety
**Author:** Hermes Agent self-analysis

## Problem

AI agent with full OS access (write_file, terminal, patch, execute_code) can corrupt production configurations. Existing approval system (tools/approval.py) only checks terminal commands via regex (rm -rf, dd, chmod). File operations (write_file, patch) bypass approval entirely. Agent can switch tools to bypass partial guards.

## Architecture

### Core Pattern: Single Gate for ALL Tools

```
Agent tools (write_file, terminal, patch, execute_code)
    ↓
A. Normalization — extract path from any tool call
    ↓
B. Path Classification — deterministic regex (not LLM)
    ├── SYSTEM (/etc/*, /opt/*, /usr/*, /var/lib/*) → BLOCK
    ├── USER (/home/*, /tmp/*, /var/www/*, project) → PASSTHROUGH
    └── UNKNOWN (everything else) → BLOCK + ask user
    ↓
C. Decision
    ├── BLOCK → user approval callback
    └── PASSTHROUGH → execution
    ↓
D. Scope Validation (optional)
    — agent declares intent ("nginx", "stalwart")
    — guard checks path matches declared scope
    — scope registry, not LLM-interpreted
```

### Key Properties

- **One classifier, all tools.** Agent cannot bypass by switching from write_file to terminal.
- **No LLM in critical path.** Path classifier is deterministic regex. Not subject to stochastic compliance.
- **Not bypassable via tool switching.** Guard inspects syscall args, not which Hermes tool was called.
- **Built-in SYSTEM_PATHS per OS.** Linux: /etc, /opt, /var/lib/system. macOS: /etc, /Library, /System. Windows: C:\Windows, C:\Program Files, C:\ProgramData.

### Coverage vs System Type

| Server type | Coverage | What it catches | What it misses |
|-------------|----------|-----------------|----------------|
| Static web (nginx) | ~70% | Direct config writes, cert changes | nginx -s reload (runtime) |
| Multi-service (nginx+Xray+mail) | ~50-60% | Config file writes | Network changes (iptables, ufw), docker, port sharing |
| Full stack with Docker | ~40-50% | Infrastructure configs | Docker port mapping, runtime changes, container configs |

### Structural Limit (proven by Prism-full May 2026)

**Knowledge × Memory × Scope ≤ 2** for any guard on a single machine:

- **Knowledge** — guard must know all service dependencies (nginx ↔ Xray ↔ certbot ↔ 443). Dependency graph always incomplete.
- **Memory** — agent must remember dependencies when making decisions. Context compression evicts knowledge.
- **Scope** — guard must block ALL dangerous changes. Path classifier misses: iptables, ufw, apt install, docker, systemctl reload, signals.

**Full solution:** Physical environment separation (staging → review/merge → production, like HiClaw by Alibaba).
**Single-server:** 60-70% coverage is achievable and is better than 0%.

### Service Knowledge Layer (v2 evolution)

Path classifier alone is insufficient: it catches *where* agent writes but not *what* breaks. Solution — persistent dependency map:

**Bootstrap paradox:** Agent doesn't know what dependencies exist because map is empty. Map can't be built without knowing what to scan.

**Solution:** First SYSTEM write triggers comprehensive snapshot:
- `systemctl list-units --state=running`
- `ss -tlnp`
- Config parsers (nginx -T, stalwart dump)
- Result → LLM Wiki entities + HippoRAG index

**Post-write diff expansion:** After each SYSTEM write, delta detection:
- New ports/files/sockets → auto-add to map
- Existing relationships → never overwrite without confirmation
- Uses `diff` tooling (not LLM) for structural changes

**LLM is ONLY used for:**
- Snapshot: structuring raw system data into entity files
- Post-write diff: semantic interpretation (what changed)
- **NOT** in pre-write critical path (pure code)

**Key insight from May 2026 session:** LLM Wiki and HippoRAG are disk storage, NOT LLM context. They are NOT affected by context compaction. Knowledge lives outside the prompt.

### Cost misestimation — critical blind spot

When evaluating guard implementation, the agent must NOT estimate in human-hours:
- **Human estimates:** 3-day sprint, code review cycle, QA pipeline, deployment window
- **Agent reality:** `/goal спроектируй и внедри` → 5 min human setup + 15-30 min agent execution

**Rule:** If agent is both architect AND implementer, estimate in agent-hours, not human-hours.

## Implementation Strategy

### Step 1 (4 hours agent time)
- Add path classifier to `model_tools.py` (tool dispatch layer)
- Cover `write_file` + `patch` — SYSTEM paths => block
- Minimal SYSTEM_PATHS for detected OS

### Step 2 (following day)
- Add path extraction from `terminal` commands (regex for >, tee, dd of=, sed -i, python open)
- Add `execute_code` scan for file operations in code

### Step 3 (optional)
- Add `chattr +i` on critical config dirs as OS-level backup
- Add `etckeeper` for /etc history + rollback

## References

- Tools/approval.py — existing 3-level approval (hardline, dangerous, smart LLM)
- Tools/file_tools.py — write_file/patch currently NOT in approval pipeline
- HiClaw v1.1.0 (Alibaba) — K8s-native agent orchestrator with environment isolation
