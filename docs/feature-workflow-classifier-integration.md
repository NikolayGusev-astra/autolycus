# Workflow Classifier Integration

**Дата:** 31 мая 2026  
**Статус:** ✅ Внедрено, 104 теста проходят

## Что сделано

Автоматическая классификация запросов пользователя и подгрузка skill в Telegram адаптере.

## Архитектура

```
Telegram message → _build_message_event()
  ↓
topic binding (config) → если есть → используем
  ↓
если None → WorkflowClassifier.classify(text)
  ↓
confidence >= 0.5 → auto_skill = profile.skill
  ↓
gateway/run.py → _load_skill_payload(skill_name) → в агента
```

## Кодовая база

| Файл | Что изменилось |
|---|---|
| `gateway/platforms/telegram.py` | `+36` строк: `_get_workflow_classifier()` + вызов в `_build_message_event()` |
| `scripts/workflow_classifier.py` | `+2` ключевых слова: `напиши`, `ecom2` |
| `tests/gateway/test_workflow_classifier_integration.py` | Новый файл, 8 тестов |

## Workflow Classifier — как работает

1. **Lazy init** — `WorkflowClassifier` создаётся при первом запросе (~2ms)
2. **Кэш** — один экземпляр на процесс (класс-атрибут `_workflow_classifier`)
3. **Regex matching** — 166 keyword rules, 0.03ms на classify
4. **Confidence** — `min(1.0, sum_weights / 2.0)`, порог 0.5

## Supported workflows

| Workflow | Skill | Триггеры | Confidence |
|---|---|---|---|
| `ford_diagnostics` | `auto-diagnostics` | форд, эксплорер, gem модуль, 5r55e | 1.0 |
| `article_writing` | `autolycus-article-writer` | статья, напиши, пост, telegra.ph, draft | 0.7 |
| `bitgn_research` | `bitgn` | bitgn, ecom1, ecom2, pac1 | 0.7 |
| `email_security` | — | почта, email, phishing, spam | 0.7 |
| `outcome_contract` | `bitgn` | outcome contract, verify gate, trust boundaries | 0.7 |
| `diagnostic_generic` | — | диагностика, не работает, ошибка | 0.7 |

## Приоритеты

1. **Topic binding** (конфиг) — всегда побеждает
2. **Workflow classifier** — только если `topic_skill is None`
3. **Порог** — confidence < 0.5 → skill не загружается

## Безопасность

- `try/except` вокруг всего блока — error не ломает message handler
- `logger.debug` для skipped, `logger.warning` для init failure
- Module-level кэш failure — не повторяет init при каждом message

## Тесты

```bash
python -m pytest tests/gateway/test_workflow_classifier_integration.py -v
# 8 passed

python -m pytest scripts/tests/ tests/gateway/test_workflow_classifier_integration.py -v
# 104 passed (96 existing + 8 new)
```
