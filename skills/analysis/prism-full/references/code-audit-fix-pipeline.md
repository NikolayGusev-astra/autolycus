# Code Audit → Prism → Premortem → Fix Pipeline

Validated workflow from session 2026-05-04: a 5-step pipeline for auditing and
remediating a multi-module system.

## Steps

### Step 1: Inspect + Test
Read all source files, run the actual system, identify what works and what breaks.

Output: list of issues with severity (CRITICAL/HIGH/MEDIUM/LOW), each with
file/line location, impact description, and a simple test that reproduces it.

### Step 2: Prism-Full (via subagent)
Delegate Prism analysis of the full system to a subagent. Give it:
- All source files (read_file)
- Current state: what works, what breaks
- Environment info: kanban state, processes, config

Prism produces: conservation law, findings table ranked by severity,
structural recommendations, architecture diagram.

### Step 3: Premortem (via subagent, in parallel)
Run a separate premortem subagent in parallel (not sequential — they're independent).
Give it the same context plus the actual runtime state (process status, memory,
disk, logs).

Premortem produces: kill scenarios ranked by severity, most likely fail-first
scenario, hidden assumptions, checklist.

### Step 4: Synthesize Fix Plan
Merge Prism findings + Premortem kill scenarios into a ranked fix plan.
Order: CRITICAL blockers first, then HIGH, MEDIUM, LOW.

For each fix item: what to change, in which file, expected effect.

### Step 5: Batch Apply + Verify
Apply fixes in priority order. After each batch: re-test, verify syntax, check
kanban state. End with a status table showing every file changed and what was
done.

## Status Table Template

After all fixes are applied, present:

| # | Статус | Изменение | Файл | Суть |
|---|--------|-----------|------|------|
| 1 | ✅ | Переписан целиком | remind.py | hermes kanban list вместо мёртвого API |
| 2 | ✅ | Добавлена функция | run.py | assignee resolution + priority из текста |
| 3 | ✅ | Оптимизация | enrich.py | Кеш Natasha моделей |
| ... | ... | ... | ... | ... |

## Lessons Learned

- **Prism and Premortem can run in parallel** — they don't depend on each other.
  The premortem finds infrastructure/runtime issues; Prism finds code-structural
  issues. Combined they cover both dimensions.

- **Always test the pipeline end-to-end before reporting "done"** — in this session,
  the pipeline worked but `remind.py` was silently broken (old API). Only a full
  consumer audit (Step 1: grep for old API endpoint in ALL files) catches these.

- **After applying batch fixes, present the status table immediately** — don't
  wait for the user to ask "где отчёт?". They want to see what changed, in what
  file, and what's left to do.

- **Priority detection needs source text, not just extracted fields** — 
  key urgency keywords may be in a different sentence than the extracted relation.
  Always check the original input text.

- **System config files (/etc/nginx/nginx.conf) can't be patched with the `patch`
  tool** — use terminal with Python sed or sed-inline with backup.
