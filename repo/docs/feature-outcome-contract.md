# Outcome Contract — BitGN-inspired Agent Safety

> Формальный протокол завершения задач для AI-агентов.
> Вдохновлено BitGN Agent Challenges (ECOM1 / PAC1).

**Репозиторий:** https://github.com/NikolayGusev-astra/autolycus
**Модули:** `/opt/autolycus/scripts/`
**Тесты:** `cd /opt/autolycus && python -m pytest scripts/tests/ -v`

## Модули

### 1. task_outcome.py — Outcome Contract

6 outcome-кодов для формального завершения задачи:

| Код | Когда использовать |
|-----|-------------------|
| `OK` | Задача выполнена успешно |
| `DENIED_SECURITY` | Отказ по соображениям безопасности |
| `DENIED_POLICY` | Отказ по политике/правилам |
| `CLARIFICATION` | Требуется уточнение от пользователя |
| `UNSUPPORTED` | У агента нет такого инструмента/возможности |
| `ERROR` | Техническая ошибка при выполнении |

API:
- `verify_outcome(outcome)` — строгая валидация
- `format_outcome(outcome)` — форматирование для пользователя (🚫 ❓ ⚠️ ❌)
- `format_trace(outcome)` — формат для логов: `[OUTCOME: denied_security] ...`
- 6 factory helpers: `outcome_ok()`, `outcome_denied_security()`, `outcome_denied_policy()`, `outcome_clarification()`, `outcome_unsupported()`, `outcome_error()`

**Тесты:** 20 тестов

### 2. run_with_outcome.py — Обёртка для скриптов

- `run_script(path)` — запускает скрипт через subprocess с таймаутом
- `run_function(fn)` — запускает функцию с перехватом исключений
- `run_with_outcome(x)` — единый интерфейс (строка=скрипт, callable=функция)

**Тесты:** 11 тестов

### 3. response_verifier.py — Verify Gate

Автоматическая проверка ответа перед отправкой:

- `verify_grounding_refs(refs)` — непустые строки, файлы существуют
- `verify_outcome_completeness(outcome)` — достаточность объяснения
- `verify_no_contradiction(outcome, context)` — message не противоречит контексту
- `verify_response(outcome, context)` — агрегирует все проверки

**Тесты:** 24 теста

### 4. trust_classifier.py — Trust Boundaries

- `classify_source(type, path)` — trusted / semi_trusted / untrusted
- `check_instruction_source(content, trust)` — поиск императивных конструкций в untrusted
- `classify_user_request(message)` — safe / suspicious / malicious
- `get_trust_annotation(path)` — type, trust, sensitive для файла

**Тесты:** 17 тестов

### 5. sanitize_with_trust.py — Production input sanitization

- `sanitize_input(message, source_type)` — блокирует prompt injection
- `sanitize_file_content(content, path)` — проверяет untrusted файлы
- `sanitize_api_response(data, url)` — проверяет внешние API

**Тесты:** 9 тестов

## Интеграция в Skills

- `/bitgn` — Outcome Contract + Verify Gate + Trust Boundaries
- `/diagnostics` (Ford Explorer) — то же + таблица доверия источников
- `clarity-flow` — Verify Gate как финальный шаг

## Статистика

- **5 модулей Python, ~800 строк**
- **81 авто-тест, 100% проходят**
- **3 навыка с интеграцией**
- **8 коммитов в main**

## Веб-страница

https://autolycus-agent.ru/outcome-contract.html
