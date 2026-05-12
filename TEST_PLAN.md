# Autolycus — Functional Test Plan

## Цель
Подтвердить что все 10 компонентов Автолика работают согласованно, 
не ломают друг друга, и сохраняют контракты.

## Компоненты под тестированием

| # | Компонент | Тип | Prio |
|---|-----------|-----|------|
| 1 | P0: file_operations stdlib | Core — file I/O | 🔴 |
| 2 | P1: Unload after use | Perf — memory | 🟡 |
| 3 | P5: malloc_trim | Perf — memory | 🟡 |
| 4 | P6: ContextWriter | Memory — context | 🔴 |
| 5 | ultra-governance: Tool Policy | Safety — dispatch | 🔴 |
| 6 | ultra-governance: RTK Filter | Perf — output | 🟡 |
| 7 | SBL | Safety — path | 🔴 |
| 8 | Sanitize pipeline | Safety — input | 🔴 |
| 9 | Sanitize MCP | Safety — MCP | 🟡 |
| 10 | ClickHouse memory provider | Memory — storage | 🟢 |
| 11 | findings_to_wiki | Memory — auto-save | 🟢 |

## Test layers

### Layer 1: Unit — isolated component tests
Каждый компонент тестируется отдельно с моками зависимостей.
Покрытие: все экспортируемые функции, все ветки, граничные случаи.

### Layer 2: Integration — plugin hooks wiring
Проверка что хук зарегистрирован, принимает правильные kwargs,
возвращает правильный тип, не крашит агента при ошибке.

### Layer 3: E2E — full pipeline on victim container
Полный цикл: user message → agent reasoning → tool dispatch → 
policy check → execution → RTK filter → response.

## Test inventory — current vs needed

| Компонент | Существующие | Нужно дописать |
|-----------|-------------|----------------|
| file_operations | 3 файла, ~200 тестов | 0 (достаточно) |
| Unload after use | 0 | 1 — verify JIT + unload |
| malloc_trim | 0 | 1 — verify call + no effect on perf |
| ContextWriter | 0 | 3 — write, search, compress |
| Tool Policy | 0 | 5 — allow, deny, simulate, enforce, audit log |
| RTK Filter | 0 | 3 — head/tail, repeat, cap |
| SBL | 1 proto (manual) | 3 — classify, snapshot, dependency |
| Sanitize pipeline | 3 файла, 26 тестов | 0 (достаточно) |
| Sanitize MCP | 1 файл | 0 (достаточно) |
| ClickHouse | 0 | 2 — write, read |
| findings_to_wiki | 0 | 2 — trigger, format |

**Итого:** существующие ~230 тестов, нужно дописать ~21.

## Execution plan

1. Написать тесты в tests/ структуре
2. Запустить на HQ (где стоит venv)
3. Исправить упавшие
4. Прогнать на kozanout
5. Залить в репу
