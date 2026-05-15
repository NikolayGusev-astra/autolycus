# Prism Full: Worked Example — External Article Analysis

## Context

Habr article «Cursor как общая среда для заказчика и разработчика» (1030922).
Goal: structural analysis of a technical-experiment article about shared Cursor workspace.

## Pipeline (4 passes)

| Pass | Type | Focus |
|------|------|-------|
| 1 | Claim Extraction & Inversion | Extract empirical claims, assume each FALSE, trace corruption, build alternatives |
| 2 | Scale Simulation (Temporal) | Run through 3 growth cycles (2nd client → 12 people → autonomous agents) |
| 3 | Missing Infrastructure Topology | Build layers 3-5 the article doesn't mention (audit, dependency graph, governance) |
| 4 | Conservation Law Search | Derive A × B = Constant from all 3 passes |

## Adversarial Key Findings

- **Retracted:** «ACLs don't prevent agent mistakes» — partially false. Write ACL blocks at syscall level. Real gap: read and file creation.
- **Added:** Article's strongest insight is separating Cursor account from Linux user (orthogonal identity). Human dimension: mutually assured limitation architecture.

## Deepest Finding

**Inverse Trust Distribution.** Developer gives client Cursor access (trust) but constrains via ACL (control). Client gives developer sudo (control) but keeps own Cursor account (privacy). Each has unilateral power in one domain, zero in another. A mutually assured limitation architecture the author didn't name.

## Post-Analysis Output

1. Wiki save: `concepts/prism-analysis-habr-1030922-cursor-workspace.md` (brief) + `concepts/prism-analysis-1030922-habr.md` (detailed)
2. 5 actionable recommendations for the author
3. index.md + log.md updated

## Tips for External Article Prism

- Fetch full article text via `curl` + strip HTML tags/scripts before analysis
- Default pipeline: Claims Inversion → Scale Simulation → Missing Infrastructure → Conservation Law
- Always name the article's implicit/unconscious design principle in the deepest finding
- End with concrete recommendations the author could actually implement (not generic advice)

## Dual-System Comparison Variant

Когда задача — не просто проанализировать статью, а **сравнить с нашей реализацией**:

### Pre-Pass: Define both systems explicitly
Перед любым анализом запиши что конкретно сравниваешь:
```
Слева: [артефакт из статьи] — его компоненты A, B, C
Справа: [наша реализация] — её эквиваленты A', B', C' (или D, E если архитектура разная)
```
Если не уверен в границах «нашего» — спроси. Пример: «наша реализация памяти» может быть memory() + HippoRAG + session_search + MemPalace — или что-то одно.

### Pipeline (4 passes, адаптированный под comparison)

| Pass | Type | Focus |
|------|------|-------|
| 1 | **Inventory & Decomposition** | Разложить обе системы на блоки: что хранит, как хранит, как находит, как забывает, как учится. Сравнить не по названиям, а по функциям. |
| 2 | **Impossibility Triangles** | Для каждой системы вывести trade-off треугольник (A × B = Constant). Показать чем жертвует каждая. |
| 3 | **Evolutionary Simulation** | Что происходит с каждой системой на масштабе (100, 1K, 100K объектов)? Где упрутся? |
| 4 | **Adversarial (mandatory)** | Атаковать собственные сравнения: не сравниваешь ли яблоки с апельсинами? Не принимаешь ли за данное разные цели систем? |

### Synthesis for comparison
Вместо единой conservation law — две, и мета-закон:
- **System A law:** что сохраняется в System A
- **System B law:** что сохраняется в System B
- **Meta-law:** что объединяет оба закона — фундаментальное ограничение, которое ни одна система не обходит

### Follow-through: не торопись с PR
После сравнения у пользователя часто возникает «а давай PR/issue?».
Обязательно уточни **куда** (репо внешней системы или своё) и **что именно**
(баг, фичу, архитектурное предложение). См. pitfall в SKILL.md «Comparison target ambiguity».

### Worked example: FSRS vs Hermes Memory
Полный разбор: `references/fsrs-vs-hermes-analysis.md` — как применялся этот pipeline
к статье habr.com/ru/articles/1031628/ (FSRS-плагин для Obsidian) против Hermes Memory System.
