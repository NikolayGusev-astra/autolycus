# Dual-System Comparison: Prism Pipeline

Когда Prism сравнивает **две технологии** (DuckDB vs SQLite, SQLite vs FSRS, etc.), а не анализирует один артефакт — стандартные пассы (Scope, Inversion, Conservation) не дают depth. Нужна специализированная pipeline.

## Optimal Pipeline (May 2026 — proven on DuckDB vs SQLite)

### Pass 1: Cognitive Workload Mapping

Не сравнивай synthetic benchmarks («DuckDB быстрее в 100x»). Установи **реальный паттерн доступа** в системе, где технология применяется:

- Read/write ratio
- Типы SQL: point lookup vs range scan vs full scan vs aggregate
- Размер данных
- Concurrency model (сколько writers, readers)
- Frequency per query type
- Ecosystem: какие фичи используются (FTS5, triggers, WAL, CTE, pragmas)

**Выход:** таблица «тип операции → частота → performance в системе A → performance в системе B»

### Pass 2: Structural Inversion (обязателен)

Построй два сценария **с временными шкалами**:

**Сценарий A:** Заменить X → Y (где X = текущее, Y = альтернатива)
- Что ломается в первые 60 секунд? (синтаксис, API, фичи)
- Что ломается через 5 минут? (миграции, триггеры, индексы)
- Что ломается через день? (backup, monitoring, NFS, деплой)
- Что НЕ ломается (но это не даёт выгоды)?

**Сценарий B:** Заменить Y → X
- Аналогично, с инверсией.

**Выход:** два списка «что сломается и почему» — с severity (catastrophic / degradation / cosmetic)

### Pass 3: Cost/Benefit with Implementation Reality

Не теоретическое сравнение. Для каждой строки в таблице:

| Критерий | Система A | Система B | Вес для данного use case |
|---|---|---|---|
| FTS5 | ✅ | ❌ | критично |
| Concurrency | ✅ WAL | ❌ single-process | критично |
| ... | | | |

**Ключевые колонки:**
- Значение критерия (✅/❌/partial)
- Миграционные затраты (LOC, дни, тесты)
- Реальный выигрыш в данном workload (а не в synthetic benchmark)

### Adversarial Pass (специфичный для сравнения)

1. **Overclaim:** «X не подходит» — а если X используется **не как замена, а как дополнение?** (DuckDB как analytical layer поверх SQLite — правильный паттерн)
2. **Underclaim:** «Y не умеет Z» — а community extension? А workaround? Оцени quality vs maturity (15 лет FTS5 vs weekend extension)
3. **Hidden assumption:** что сравниваемые системы делают одно и то же. А если X спроектирован для OLTP, а Y — для OLAP?
4. **Context collapse:** что было бы, если бы обе системы были спроектированы для гибридного workload? (HTAP)
5. **Vendor lock-in maturity:** ecosystem эффект (6 модулей на SQLite vs 0 на DuckDB)

## Когда применять

- Пользователь спрашивает «почему X лучше Y для задачи Z?»
- Есть codebase с embedded хранилищем, и кто-то предлагает заменить его на «более быстрый» engine
- Выбор database engine для нового компонента (проверить, какой workload реально будет)

## Case studies

- `references/fsrs-vs-hermes-analysis.md` — FSRS (spaced repetition) vs Hermes Memory (dual-system comparison).
  Тренировочный пример: сравниваются системы с fundamentally разными целями, результат — «не заменять, а интегрировать».

## Pitfalls

- **Synthetic benchmark blindness:** «DuckDB быстрее SQLite в 100x» — benchmark на 10M rows GROUP BY, а workload — 84K INSERT с point lookup. Разница 0x.
- **Feature-equivalence trap:** оба умеют SQL, но один не умеет FTS5/trigrams/trigger-based auto-indexing. Это не «almost same», это critical gap.
- **Migration cost underestimation:** «просто поменять import» — а 3K LOC с PRAGMA, WAL checkpoint, FTS5 triggers, LIKE escape, schema migration? Реальность: недели, не часы.
- **Use case scope creep:** DuckDB может ВСЁ — но нужно ли ему ВСЁ для этой задачи? (см. Cognitive Workload Mapping — он отсекает irrelevant capabilities)
