# Audit Before Build — Anti-duplication Check

When building a custom component on top of Hermes Agent (or any extensible platform),
run this checklist BEFORE writing any code.

## The Checklist

Before creating any new module:

- [ ] Does the CLI provide this? `hermes <verb> --help`, `hermes kanban --help`
- [ ] Does the Gateway API provide this? Check `localhost:8642/health`, list routes
- [ ] Does an MCP server provide this? `mcp_mempalace_*` tools
- [ ] Does a skill already exist? `skills_list()`
- [ ] Does a built-in tool exist? Check `tools/` in Hermes Agent codebase
- [ ] Does the PLATFORM hook system provide this? Check `agent/*.py` for ABCs and hooks:
  - `agent/memory_provider.py` — MemoryProvider hooks (sync_turn, on_session_end, on_pre_compress)
  - `agent/context_engine.py` — ContextEngine hooks
  - `agent/context_compressor.py` — compression pipeline
- [ ] Is there an existing plugin in `plugins/` that already does this?
- [ ] Does `run_agent.py` call a hook that does exactly what I need? (Search for `on_` methods)

## Case Study: Memory Fix (May 2026)

**The mistake:** After Prism analysis revealed memory was broken, we designed a 4-component
fix plan with cron, systemd path units, backup, health monitoring, and ~600 LOC.

**The reality:** Hermes Agent already had `on_session_end` and `sync_turn` hooks in
`MemoryProvider` base class, and `commit_memory_session` was called BEFORE compression
in `run_agent.py:9151`. We just needed a provider that overrides them.

**The fix:** 80 lines of Python, 0 new infrastructure.

**Lesson:** Before building ANY infrastructure, check:
1. `agent/memory_provider.py` — what hooks exist
2. `agent/memory_manager.py` — how they fire
3. `run_agent.py` — where they're called in the loop
4. `plugins/memory/<name>/` — existing provider implementations (honcho is the only one)

## Evidence from this session

| Custom component | LOC | What Hermes already had |
|------------------------|-----|------------------------|
| api_bridge.py | 315 | Gateway port 8642 |
| people_store.py | 200 | MemPalace KG |
| bot.py (duplicate TG bot) | 284 | Gateway Telegram integration |
| SPA kanban board | ~700 | hermes kanban is CLI-only by design |
| Memory cron+plugin+backup plan | ~600 | 80-line MemoryProvider with sync_turn/on_session_end |

## The fix

Before creating any file:
1. `curl -s localhost:8642/health` — check gateway
2. `grep -r 'def on_' agent/*.py | grep -v __pycache__` — find all hooks
3. `ls plugins/memory/` — check existing providers
4. `grep -rn 'on_pre_compress\\|on_session_end\\|sync_turn' run_agent.py` — check where hooks fire

## Full Compound Pipeline (validated May 2026)

When the task is "fix the memory/system/infrastructure", use this end-to-end workflow.
Each step is a distinct pass; do not skip or merge.

```
1. RECON              — gather facts (state.db size, cron status, file timestamps)
2. PLATFORM AUDIT     — check existing hooks/providers/plugins BEFORE designing
3. PRISM on the plan  — delegate to fresh subagent (Cost/Benefit, Alternatives, Adversarial)
4. PRISM on YOUR plan — after revision, check for overengineering
5. PREMORTEM          — "this failed 6 months from now. Why?"
6. BUILD              — minimal LOC on existing hooks (target 80-100 lines)
7. ARTICLE            — document what exists now, not "how bad it was before"
```

### Step detail: what to check WHERE

**Steps 1-2 (RECON + PLATFORM AUDIT):**
Performed in code, not documentation:
```
# Gateway health
curl -s localhost:8642/health

# All hooks in the platform
grep -r 'def on_' agent/*.py | grep -v __pycache__

# Existing providers
ls plugins/memory/

# Where hooks fire in the main loop
grep -rn 'on_pre_compress\|on_session_end\|sync_turn' run_agent.py

# Size counters
wc -l AGENTS.md && sqlite3 state.db "SELECT count(*) FROM sessions"
```

**Step 3 (Prism on plan):** Delegate to fresh subagent. Minimum passes:
- Cost/Benefit of each proposed component
- Hidden dependencies and order analysis
- Failure mode analysis (top 2 per component)
- Adversarial: attack every assumption

**Step 4 (Prism on YOUR plan):** Target: is it still overengineered? Key questions:
- Does this duplicate existing platform capabilities?
- Did I verify the foundation before building on it?
- The 52% Rule: if >40% LOC duplicates platform, fix is deletion

**Step 5 (Premortem):** "Failed. Why?" Generate 10-15 failure modes.
- Most likely failure (probability x impact)
- Most dangerous failure (highest impact)
- THE hidden assumption (not technical — organizational)
- Pre-flight checklist

**Step 6 (Build):** Target on existing hooks:
- MemoryProvider with sync_turn() (per-turn) + on_session_end() (flush) = ~50 lines
- HippoRAG cron via system crontab, not Hermes cron (no LLM burn)
- Write atomically to MEMORY.md (same format: \n§\n, mkstemp+os.replace)
- Test: loads, writes, does not corrupt format

**Step 7 (Article):** Write without referring to "how bad it was before":
- Just analytics and how to configure
- Numbers (LOC, disk, latency, cost)
- The three files that matter
- What I would do differently
