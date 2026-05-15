# Protocol Fallback Pattern

## Проблема

LLM-генерация протокола из текста часто возвращает пустые поля ("НЕ ОПРЕДЕЛЕНО"), если текст не похож на стенограмму совещания (список контактов, заметки, файл с данными). Пользователь видит пустой ответ.

## Решение: двухуровневый fallback

```
Текст
  │
  ├── >200 символов ──→ LLM протокол (protocol.py)
  │                       │
  │                       ├── есть участники/решения/задачи → показать протокол
  │                       │
  │                       └── пусто → Natasha NER (run_enrich) → показать сущности
  │
  └── <200 символов ──→ Natasha NER (run_enrich) → показать сущности
```

## Реализация

В bot.py `_auto_analyze_text()`:

```python
proto = generate_protocol(text)
has_participants = proto.get("participants") and proto["participants"] != ["НЕ ОПРЕДЕЛЕНО"]
has_decisions = bool(proto.get("decisions"))
has_tasks = bool(proto.get("tasks"))

if not has_participants and not has_decisions and not has_tasks:
    # Fallback: Natasha NER
    entities = run_enrich(text, extract_relations=False)
    # показать людей и организации
else:
    # показать протокол
```

## Когда применять

- Боты, которые принимают произвольный текст (не только стенограммы)
- Системы с автоопределением типа входа
- Любой пайплайн, где LLM может честно сказать "НЕ ЗНАЮ"

## Pitfall

Natasha NER работает только для русского языка. Для английского или смешанного языка нужен другой NER (spaCy, GLiNER) или LLM-based extraction.
