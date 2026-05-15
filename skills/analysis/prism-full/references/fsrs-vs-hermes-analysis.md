# Dual-System Comparison: FSRS for Obsidian vs Hermes Memory System

**Cross-ref:** См. `references/dual-system-comparison.md` — общая методология сравнения двух систем.
Pipeline из этой сессии (Cognitive Workload → Structural Inversion → Cost/Benefit) применяется здесь post-hoc.

**Source:** habr.com/ru/articles/1031628/ — плагин интервального повторения FSRS для Obsidian от EvgeneKopylov

**Target system:** Hermes Agent Memory (memory() + FTS5 + HippoRAG v2 + LLM Wiki + MemPalace)

**Date:** 2026-05-06

## Pre-Pass: Define Both Systems

### FSRS + Obsidian Plugin
- **Что хранит:** history of review sessions (date + rating 0-3) in YAML frontmatter of .md notes
- **DSR model:** Difficulty (permanent per card), Stability (grows with successful reviews), Retrievability (decays exponentially)
- **Storage:** YAML frontmatter in native Obsidian .md files — portable, human-readable, no external DB
- **Retrieval:** SQL-like queries via fsrs-table blocks
- **Forgetting model:** Explicit — Retrievability drops; card resurfaces when below threshold
- **Scale limit:** ~1-10K cards per user. FSRS is per-card optimization

### Hermes Agent Memory
- **Что хранит:** key-value facts (memory()), session transcripts (state.db FTS5), co-occurrence graph (HippoRAG v2, 598K edges), structured wiki (~/wiki/, markdown with frontmatter), semantic storage (MemPalace via chromadb)
- **Storage:** SQLite + plain text files + chromadb + networkx graph. Multiple backends, single purpose.
- **Retrieval:** memory() injection (every turn), FTS5 (session_search), Personalized PageRank (assoc_search), HippoRAG-lite, semantic search (MemPalace)
- **Forgetting model:** None explicit — MEMORY.md capped at ~2KB, session_search returns top-k. Eviction, not forgetting.
- **Scale limit:** ~100K sessions before graph traversal becomes heavy. No time-decay on weights.

## Pass 1: Inventory & Decomposition

| Function | FSRS Plugin | Hermes Memory |
|----------|-------------|---------------|
| Storage format | YAML frontmatter in .md | SQLite + text + chromadb |
| Retrieval | SQL queries + due-date sorting | FTS5 + PPR + semantic + injection |
| Learning algo | DSR bayesian optimization | findings_to_wiki auto-save (no optimization) |
| Time model | Explicit exponential decay | None (flat recency) |
| Eviction | Implicit: cards below threshold don't show | Explicit: MEMORY.md compression |
| Portability | Native .md — any editor can read | SQLite dump or wiki export needed |

## Pass 2: Impossibility Triangles

**FSRS:** `Recall × Efficiency × Portability = Constant`
- High recall → more reviews → less efficiency
- Portability (YAML in .md) limits algorithmic complexity
- FSRS shifts the point on this surface but can't escape it

**Hermes:** `Recall Breadth × Retrieval Latency × Autonomy = Constant`
- Store everything → slow retrieval + noise in context
- Fast retrieval → aggressive compression → lost details
- Autonomous save → no quality gate → noise

**Meta-law:** *Системы памяти жертвуют либо точностью актуальности, либо полнотой охвата.*
- FSRS жертвует полнотой (только то, что учишь)
- Hermes жертвует точностью актуальности (всё хранится, но не знает что устарело)

## Pass 3: Evolutionary Simulation

### FSRS at scale
- 1K cards: excellent
- 10K: manageable with tags/filters
- 100K: per-card DSR computation becomes heavy; SQL without indexes breaks
- **Design limit:** Individual learner, not enterprise KB

### Hermes at scale
- 1K sessions (current 6.3K): fine
- 10K sessions: HippoRAG at 598K edges, still fine
- 100K sessions: PPR traversal O(V+E) becomes heavy; MEMORY.md compression breaks
- 1M sessions: needs sharding or eviction model
- **Missing:** time-decay on HippoRAG weights, TTL on facts, retrievability-based eviction

## Pass 4: Adversarial

**Overclaim 1:** "FSRS and Hermes solve different problems — incomparable"
- *Evidence against:* Both have a temporal dimension. Hermes doesn't model fact staleness; FSRS models it explicitly. The comparison is valid specifically at this point.
- *Verdict:* Partially retracted. The shared ground is **time-aware retrieval** — FSRS has it, Hermes doesn't.

**Overclaim 2:** "FSRS doesn't scale"
- *Evidence against:* It's not designed to. This is a feature, not a bug. Calling it a limitation misses the point.
- *Verdict:* Retracted. FSRS is deliberately personal-scale. The comparison to infrastructure-scale Hermes is category error unless scoped.

**Overclaim 3:** "No forgetting model is a problem for Hermes"
- *Evidence against:* MEMORY.md compression + top-k session_search IS an eviction model. It's not gradual forgetting, but for LLM context, abrupt eviction may be better (LLMs need exact facts, not probabilistic ones).
- *Verdict:* Refined. The problem is not *absence* of forgetting, but *absence of priority* — what to keep and what to evict is arbitrary.

## Deepest Finding

**Hermes не имеет модели забывания, но должен иметь — не для удаления данных, а для приоритизации в контексте.**

FSRS решает «что показать *сейчас* на основе вероятности забывания».
Hermes решает «что вставить в *контекст* на основе релевантности».

Но релевантность должна взвешиваться по времени — свежий факт про текущий проект важнее старого. Сейчас у Hermes этого нет: memory() возвращает всё, а hipporag ищет без time-decay.

## Actionable Borrowings

1. **Time-weighted retrieval** — memory() facts get timestamp decay
2. **Explicit TTL** — `memory(fact, expires="2026-12-31")`
3. **Retrievability score for HippoRAG** — weight edges by recency of session
4. **Categorised MEMORY.md limits** — user profile 2KB + working facts 2KB, not flat cap
