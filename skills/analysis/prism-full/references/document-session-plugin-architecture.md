# Document Session Plugin Architecture

## Prism Case Study (May 2026)

### The Problem

Hermes Agent has 4 file tools (read_file, write_file, patch, search_files) — all atomic, no streaming. Writing a 100-page report (~125K tokens) via write_file was impossible: the model would truncate on every retry because even with boosted max_tokens, the output budget was insufficient for a single tool call.

External research: Claude Code, Cline, Aider, Codex CLI, Cursor, LangChain, OpenHands — none has a session-based streaming file write. The document generation problem is unsolved in the AI agent space.

### Prism Passes

| Pass | Focus | Key Finding |
|------|-------|-------------|
| Implementation Surface | Hermes plugin API (register_tool, register_hook) | Plugin is the right isolation boundary — survives upstream merges |
| Failure Mode Inventory | crash, concurrent sessions, cross-ref staleness, template missing | 9 failure modes found, 6 fixable, 2 structural |
| Architectural Invariant | What MUST be true for the design to work | "No tool call generates >15K tokens" — this avoids truncation structurally |
| Trade-off Matrix | Plugin vs tools vs core change vs skill-only | Plugin wins on all axes: isolation, UX, testability, customization |

### Conservation Law

**Structured generation avoids truncation by construction.** If each `file_doc_write` generates one section (<15K tokens), the length_continuation problem never arises. The design doesn't fix truncation — it makes truncation impossible by changing the generation architecture from monolithic to section-by-section.

### Architecture

```
plugins/doc_session/
├── __init__.py              # register(ctx) → 6 tools + 1 hook
├── session_manager.py       # DocSessionManager: CRUD, plan, state
├── doc_tools.py             # Handlers: create, write, rewrite, finalize, status, resume
├── store.py                 # Persistence: ~/.hermes/docs/sessions/*.json + content/*.md
├── templates/               # YAML шаблоны (quarterly-report, meeting-minutes, research-analysis)
└── export.py                # Экспорт: .md → .pdf/.docx через pandoc
```

Toolsets: `file` (read/write/patch/search) and `doc` (create/write/rewrite/finalize/status/resume) — separate toolsets loaded independently.

### Key UX Flows

1. **New from template:** `file_doc_create report.md template="quarterly-report"` → agent loads plan → writes sections one-by-one
2. **Based on old:** `file_doc_create report.md source="report-2025.md"` → agent reads old, adapts plan, rewrites
3. **Multi-source synthesis:** `file_doc_create analysis.md sources=["data.xlsx", "research.pdf", "notes.md"]`
4. **Section revision:** `file_doc_rewrite report.md section="risks" instruction="add regulatory risks"`
5. **Crash recovery:** `file_doc_resume report.md` — reads persisted state, continues from last incomplete section

### Failure Modes Found by Prism

| # | Failure | Severity | Fix |
|---|---------|----------|-----|
| 1 | Crash between chunk_write and state save | Medium | Transactional save: write tmp → fsync → rename |
| 2 | Concurrent sessions on same file | High | file_state.lock_path() on session_id |
| 3 | Cross-section references stale after rewrite | Medium-High | LLM verification during finalize |
| 4 | Model writes without a plan | Medium | Validate plan exists before allowing writes |
| 5 | Model switch between chunks | Low | Store model_id; warn on mismatch |
| 6 | Pandoc missing for export | Low | Graceful .md-only fallback |
| 7 | No section quality assessment | Low-Medium | file_doc_review hook (optional Tier 2) |

### Deepest Finding

The design doesn't fix truncation — it **avoids** truncation by structuring the generation task so no single tool call can produce more than ~15K tokens. This is the inversion of the original approach (which tried to increase the cap). The Prism adversarial pass confirmed this: the conservation law is not about more tokens, it's about different architecture.

### Related Reading

- `analysis/whatif/references/code-change-premortem.md` — F9: formula cap never reached
- Industry research: /root/large-doc-generation-research.md (6 patterns from Claude Code, Cline, Aider, Codex, OpenHands, LangChain)
