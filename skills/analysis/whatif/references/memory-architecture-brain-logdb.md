# Agent Memory Architecture: Brain → Log DB Convergence

**Created:** 2026-05-07
**Session:** Premortem on memory architecture at scale (1 → 1000 users)
**Key insight:** Memory = log DB + aggregation. Brain is the reference architecture.

## The Convergence

Three independent conversations converged on one architecture:

| Source | Design | What it maps to |
|--------|--------|-----------------|
| Claude's answer (screenshot) | 3-tier: anchor layer + flat collection + deferred realms | Premature optimization, creates two sources of truth |
| Hermes Agent (actual code) | MEMORY.md + state.db + hipporag + MemoryProvider plugin | Works at 1 user, breaks at 50+ |
| Brain neuroscience | Parallel encoding + offline consolidation + pattern completion | Log DB + materialized views |

## Brain → System Map

| Brain system | Function | System equivalent |
|-------------|----------|-------------------|
| Working memory (PFC) | Current operation, ~7 items, volatile | LLM context window |
| Hippocampus (episodic) | All events, fast-map one-shot, time-stamped | ClickHouse `events` table — append-only, TTL 90d |
| Neocortex (semantic) | Patterns extracted from episodes, slow, consolidated | ClickHouse `MATERIALIZED VIEW` — aggregations over events |
| Basal ganglia (procedural) | Skills, how-to, non-verbalizable | SKILL.md files |
| Hippocampal replay (sleep) | Offline re-examine, consolidate | Cron: `INSERT ... SELECT ... GROUP BY` into semantic table |
| Forgetting (Ebbinghaus) | Exponential decay | `TTL` + partition drop |
| Pattern completion | Partial cue → full recall | HippoRAG PPR / vector search `cosineDistance()` |
| Emotional tagging | Amygdala marks salience | `tags[]` column — built-in log labels |
| Source monitoring error | False memories from imagination | `WHERE role = 'user'` — never extract facts from assistant |

## May 2026 Update: LLM Annotates at Write Time

The architecture above was refined by a critical insight (user Николай, 2026-05-07):

**The LLM is ALREADY processing the turn when the memory write happens. It can annotate metadata at zero additional cost.**

Instead of offline consolidation (materialized views), the LLM extracts at generation time:
- `topic` — what this conversation is about
- `importance` (0-1.0) — how significant this seems
- `is_repeat` — is the user referencing a past conversation?
- `frequency` — how many times this topic has come up
- `related_topics[]` — what this connects to
- `emotion` — emotional valence of the exchange

```python
# In the agent's response, before sending to user:
# The last block is <memory_metadata>...</memory_metadata>
# Stripped before delivery, written to ClickHouse

response = llm.generate(messages)
metadata = extract_tag(response, 'memory_metadata')
user_visible = strip_tag(response, 'memory_metadata')

# Metadata is zero-cost — already generated as part of the response
clickhouse.insert(user_id=user.id, ts=now(), content=user_visible,
                  **metadata)
```

### Why this changes everything

The "impossible triangle" (fast writes × fast reads × contextual relevance) collapses:

| Operation | Before (ClickHouse alone) | After (LLM annotates at write) |
|-----------|--------------------------|-------------------------------|
| Write | Raw insert, no structure | Insert + metadata — LLM already processed the turn |
| Read | Cosine distance / MV — slow, approximate | `ORDER BY importance * frequency DESC` — milliseconds |
| Relevance | Determined at read time (embedding scan) | **Determined at write time** — LLM understood context |

### How prefetch changes

```sql
-- Before: embedding scan or materialized view
SELECT content FROM events
WHERE user_id = 'u1'
ORDER BY cosineDistance(embedding, {query_vec})
LIMIT 5

-- After: pre-computed weight, direct ORDER BY
SELECT content FROM events
WHERE user_id = 'u1'
ORDER BY importance * log(2 + frequency) DESC, ts DESC
LIMIT 5
```

No embedding index needed. No materialized view. No PPR graph.

### Pseudo-reinforcement protection

The risk: LLM annotates what IT considers important = circular reasoning.

Mitigation:
1. **Annotation is on user message, not assistant response** — LLM doesn't decide importance, it extracts signals the user gave
2. **Frequency is an objective SELECT query** — `SELECT count(*) WHERE topic = X` — not LLM memory
3. **Confidence decay on user silence** — if user never returns to topic → automatic decay
4. **External validator** — low-cost model or rule-based check on annotation consistency

### Implication for the architecture

The LLM turns from a *consumer of memory* into an **active annotator of memory**. It's not just reading from ClickHouse — it's writing metadata that makes future reads cheaper, faster, and more relevant.

This is the closest analogy to the brain's encoding: the hippocampus doesn't just store raw data — it encodes with spatial and temporal context, emotional valence, and relational links, all at the moment of experience.

## Why This Architecture Wins

### Single DB, not a stack
- ClickHouse replaces: chromadb, pinecone, faiss, hipporag, state.db, MEMORY.md (as persistence), Redis cache, log aggregator
- One query language (SQL), one retention policy, one backup strategy
- 16+ years of production hardening (Yandex → ClickHouse Inc.)

### Why everyone reinvents this
1. **ML engineers don't know SRE tools.** Chromadb/faiss/pinecone are ML-native. ClickHouse is "for logs". These worlds don't talk.
2. **Marketing > engineering.** "Hippocampal RAG" sells better than "MATERIALIZED VIEW WITH TTL".
3. **Different names for the same need.** RAG, memory, history, context — all read recent/related data. Log DB does it all.
4. **Semantic search is a real edge case.** 80% of memory queries are "give me last week by project". 20% need embeddings. Log DB handles 80% natively.
5. **Single user doesn't need scale.** Most projects die at <10 users, so SQLite + flat files survive.

## Key Design Rules

```
1. SYNC_TURN → INSERT INTO events (append-only, async)
   - Never extract facts from assistant responses (source monitoring error)
   - Never write to "facts" table from sync_turn (consolidation is offline)
   - One table: user_id, ts, session_id, role, content, embedding, tags[]

2. PREFETCH → SELECT FROM events WHERE ...
   - context: `ago(7d) AND MATCH '...'` — fast text search
   - semantic: `ORDER BY cosineDistance(embedding, {query}) LIMIT 3`
   - pattern: read from materialized view (pre-consolidated)

3. OFFLINE CONSOLIDATION → MATERIALIZED VIEW / cron:
   - Extract patterns from events: repeated topics, confirmed facts
   - Correlate user statements across sessions
   - Expire via TTL, no manual forgetting logic

4. MULTI-TENANT → PARTITION BY user_id
   - Not "1 agent + routing" — each profile = independent agent instance
   - 1000 users = 1000 brains, not 1 brain with schizophrenia
   - Shared LLM inference (batch), isolated memory stores

5. FALSE MEMORY PROTECTION → confidence from user evidence
   - `SELECT count(*) FROM events WHERE role='user' AND content LIKE '%X%'`
   - If 0 user mentions → confidence=0.1
   - If 3+ user mentions → confidence=0.9
   - No cascade delete needed — just query frequency

6. CROSS-PROFILE RECALL → explicit shared scope
   - Default: isolated per-profile
   - Optional: shared:{user_id} scope for intentional cross-profile
   - User chooses per-entry: private vs shared
```

## When NOT to use ClickHouse for memory

- Single user (<5) — SQLite + flat file is lighter, zero infra
- Read > write ratio extremely low (user reads memory 1×/day)
- Need real-time UPDATE/DELETE (ClickHouse is append-optimized)
- Team doesn't run ClickHouse (yet) — Loki + Postgres can sub in

## Case Study: Hermes Agent (May 2026)

Had 4 levels: MEMORY.md → HippoRAG → LLM Wiki → MemPalace
P(all work) = 0.42%. MemPalace = 96% duplicates of state.db.

Prism found: 52% Rule violation (MemPalace rebuilds what state.db already does).
Premortem found: at 100 users, hipporag cron misses intervals; at 500, dead.
Fix: removed MemPalace, kept MEMORY.md + hipporag + state.db (3 → 2 active levels).
Next step: ClickHouse for >100 users, replacing hipporag + state.db with one DB.

### ⚠️ ClickHouse implementation pitfalls (May 2026)

A full Prism + Premortem was run on the ClickHouse provider plan. Four structural failure modes were found:

1. **`prefetch()`/`sync_turn()` don't accept `user_id`** — provider relies on `self._user_id` from `initialize()`, which breaks under gateway LRU reuse → data leak between users
2. **state.db (SQLite FTS5) remains shared** — `session_search()` bypasses external provider, leaking data across all users
3. **Connection pool per AIAgent** — per-request `httpx.AsyncClient` exhausts ClickHouse HTTP pool at 1000+ concurrent users
4. **Provider conflict** — `MemoryManager` allows exactly 1 external provider; findings_to_wiki already occupies the slot

See `references/clickhouse-provider-implementation.md` for the full analysis with code locations, narratives, and fixes for each failure mode.
