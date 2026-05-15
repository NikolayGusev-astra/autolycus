# ClickHouse Memory Provider: Implementation Premortem

**Created:** 2026-05-08
**Session:** Deep-dive on multi-tenant ClickHouse memory provider for Hermes Agent
**Companion to:** `references/memory-architecture-brain-logdb.md` (architectural vision)

## Context

Goal: Build a ClickHouse-backed `MemoryProvider` for Hermes Agent as a PR to the upstream repo. Multi-tenant isolation via `PARTITION BY user_id`. LLM annotates at write time (topic, importance, frequency, is_repeat, emotion). Prefetch by `ORDER BY importance*frequency DESC`.

## Codebase Reality Check (Prism Pass 1)

The codebase already has infrastructure for multi-user — but with critical gaps:

| Component | Status | Location |
|-----------|--------|----------|
| Gateway → user_id | ✅ | `gateway/run.py:1454` — passes `user_id=str(source.user_id)` |
| AIAgent stores user_id | ✅ | `run_agent.py:1032` — `self._user_id` |
| user_id → initialize | ✅ | `run_agent.py:1788` — `_init_kwargs["user_id"] = self._user_id` |
| prefetch(query, session_id) | ❌ **NO user_id** | `memory_provider.py:92` — contract lacks user_id param |
| sync_turn(user, asst, session_id) | ❌ **NO user_id** | `memory_provider.py:114` — contract lacks user_id param |
| queue_prefetch(query, session_id) | ❌ **NO user_id** | `memory_provider.py:106` — contract lacks user_id param |
| sync_all called after every turn | ✅ | `run_agent.py:4753` |
| MemoryManager: 1 external provider | ⚠️ | Only one non-builtin provider allowed at a time |
| state.db (SQLite FTS5) | ❌ **Shared across all users** | Not isolated, not overridable by external provider |
| findings_to_wiki is active | ⚠️ | `memory.provider: findings_to_wiki` — already occupies the one external slot |

**Critical finding:** The provider contract (`MemoryProvider` ABC) has `session_id` in prefetch/sync_turn parameters but NOT `user_id`. Providers that need per-user isolation must rely on `self._user_id` cached during `initialize()` — which is fragile under gateway LRU reuse.

---

## Premortem: 4 Failure Modes

Each assumes the plan "implement ClickHouse MemoryProvider, set `memory.provider: clickhouse`, deploy to 100+ users" has already failed.

### 🔴 Failure #1: Data leak via stale `self._user_id`

**Narrative:**
1. Provider stores `self._user_id` from `initialize(**kwargs)`
2. Gateway LRU reuses an AIAgent for User B without calling `initialize()` again
3. `self._user_id` still holds User A's ID
4. `prefetch()` builds `WHERE user_id = 'A'` — User B sees User A's history

**Underlying assumption:**
> "Provider lives exactly one session. `initialize()` is called once. The cached `user_id` stays correct for the provider's entire lifetime."

**Reality:** Gateway LRU / agent caching / session reuse all violate this. `initialize()` can be called multiple times, or not called when an agent is reused. `self._user_id` is shared mutable state.

**Fix:** `prefetch(query, session_id)` → `prefetch(query, session_id, user_id)`. `sync_turn()` → add `user_id` parameter. Provider must NOT rely on cached state from `initialize()` for per-request identity.

### 🔴 Failure #2: state.db — the silent second channel

**Narrative:**
ClickHouse stores isolated per-user data. But `session_search()` internally queries the shared SQLite `state.db` (FTS5). It does NOT call `memory_manager.prefetch()` — it bypasses the external provider entirely. As soon as a second user runs `/search` or the agent calls `session_search()` internally, they see sessions from ALL users.

**Underlying assumption:**
> "Setting an external memory provider replaces the built-in memory mechanisms."

**Reality:** External provider supplements, not replaces. `session_search`, `assoc_search`, state.db — all remain active and shared. Only `prefetch()` and `sync_turn()` are overridden.

**Fix:** Either (a) patch session_search to respect user_id via WHERE clause in state.db, (b) make external provider able to override session_search, or (c) split state.db per user.

### 🟡 Failure #3: Connection pool exhaustion

**Narrative:**
Gateway creates a new AIAgent per request. Each AIAgent → `initialize()` → new `httpx.AsyncClient` → new TCP connection to ClickHouse HTTP interface. Under 1000 concurrent users → 1000+ open TCP connections. ClickHouse HTTP server defaults to ~100 max concurrent connections. New connections fail with socket hang-up or 429.

**Underlying assumption:**
> "Per-request AIAgent creation is safe for I/O-heavy providers."

**Reality:** `httpx.AsyncClient` is not free — it manages a connection pool per instance. 1000 clients = 1000 pools, even if most are idle. TCP handshake overhead kills connection acceptance at high concurrency.

**Fix:** Use a shared connection pool/singleton at the provider level (class-level `_client`), or integrate with Hermes Agent's existing connection management. Not per-agent.

### 🟡 Failure #4: Provider conflict — findings_to_wiki occupies the slot

**Narrative:**
`MemoryManager` allows exactly one external (non-builtin) provider. Current config has `memory.provider: findings_to_wiki`. Switching to `clickhouse` disables wiki-article generation. Both cannot coexist — unless the ClickHouse provider also implements findings_to_wiki functionality.

**Underlying assumption:**
> "Multiple external providers can coexist for different concerns (storage vs. wiki article generation)."

**Reality:** `MemoryManager.__init__` enforces one-external-provider limit. No priority, no layering, no separation of concerns.

**Fix:** Either (a) extend MemoryManager to allow 2 external providers with distinct responsibilities, or (b) absorb findings_to_wiki's functionality into the ClickHouse provider (write-to-CH, emit-wiki-digest via separate method).

---

## Synthesis: What a multi-user ClickHouse provider MUST do

1. **Accept user_id explicitly** in every method that needs it (prefetch, sync_turn, queue_prefetch) — never rely on `self._user_id` from `initialize()`
2. **Supply user_id in SQL queries** — `WHERE user_id = {user_id}` + partition pruning
3. **Use a shared connection pool** — one client singleton per provider class, not per AIAgent
4. **Address the state.db gap** — either isolate by user_id or document that session_search remains shared
5. **Resolve findings_to_wiki conflict** — architect the provider to either coexist or absorb the wiki pipeline
6. **Test with 2+ concurrent users** on dev, not just single-user happy path

## Key files in the codebase

| File | Role | Multi-user relevance |
|------|------|---------------------|
| `agent/memory_provider.py` | ABC — define the contract | prefetch/sync_turn need user_id param |
| `agent/memory_manager.py` | Orchestrates providers, 1-external limit | Needs extension for 2+ providers |
| `run_agent.py` | AIAgent lifecycle, calls prefetch/sync | Already passes user_id to initialize |
| `gateway/run.py` | Routes messages → AIAgent | Already maps platform identity → user_id |
| `plugins/memory/findings_to_wiki/` | Current active provider | Reference implementation + conflict source |
| `plugins/memory/__init__.py` | Provider discovery & loading | Pattern for new plugin |
