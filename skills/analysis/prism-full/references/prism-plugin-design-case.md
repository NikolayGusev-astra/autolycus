# Prism for Plugin/Service Design — Doc Session Case Study

## Context (May 2026)

Designed a document-session plugin (plugins/doc_session/) for Hermes/Autolycus
that generates multi-page documents section-by-section to avoid truncation.

## Problem

write_file replaces entire files. For 50+ page documents (100KB+), the model
must output 125K+ tokens in one tool_call — exceeding any model's output budget.
The existing length_continuation mechanism boosts max_tokens but the arithmetic
chain (max_tokens=None → 4096, boost=4096×4=16384) never reaches the 32768 cap.

## Prism Passes Used

### Pass 1 — Implementation Surface
Mapped each component onto Hermes plugin API. Found: plugin (not tools/ or core)
is the only option with upstream-resistance. 6 tools + 2 hooks registered via
ctx.register_tool() and ctx.register_hook().

### Pass 2 — Failure Mode Inventory
Identified 8 failure modes: crash between chunk and save, concurrent sessions,
cross-section references after rewrite, model switch between chunks, etc.
Each has a severity and fix.

### Pass 3 — Architectural Invariant Check
Found 4 invariants: (1) session survives crash, (2) model chooses sections,
(3) sections are independent write order, (4) no tool_call exceeds 15K tokens.
Invariant (4) is the conservation law.

### Pass 4 — Trade-off Matrix
Compared Plugin vs New tools vs Core change vs Skill-only across 6 criteria.
Plugin won on upstream-resistance and isolation.

### Adversarial Pass
Retracted: "Plugin is fully isolated" → depends on registry API stability.
Retracted: "Adjacent sections inject as full context" → replaced with summaries.
Added: Toolset separation (file vs doc) to reduce cognitive load.
Added: file_doc_review for quality checking.

## Key Findings

| Finding | Severity | Type |
|---|---|---|
| Crash between chunk_write and state save | Medium | Fixable (transactional save) |
| Concurrent sessions on same file | High | Fixable (lock on session_id) |
| Cross-section references after rewrite | Medium-High | Fixable (LLM verification in finalize) |
| Model doesn't know plan → writes chaotically | Medium | Fixable (validate plan) |
| No quality check for individual sections | Low-Medium | Fixable (file_doc_review) |

## Conservation Law

**No single tool_call exceeds 15K tokens.** Section-based generation avoids
truncation by construction — the problem is not treated at the output level
but at the architecture level.

## How It Was Different From Code Prism

| Aspect | Code Prism | Service Design Prism |
|---|---|---|
| Pass 1 | Codebase inventory | Plugin API mapping |
| Pass 2 | Bug finding | Failure mode enumeration |
| Pass 3 | Structural invariants | Architectural invariants |
| Pass 4 | Cost/benefit of fixes | Trade-off matrix (4 impl options) |
| Adversarial | Find missed bugs | Retract overclaims, add missing features |

## Result

- Plugin implemented: 8 files, 1736 lines
- Tests: 43 (30 unit + 12 hook + 9 E2E)
- 3 YAML templates
- All upstream-resistant
