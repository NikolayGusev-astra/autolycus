# Good-Enough Paralysis — Full Session Case Study (May 2026)

## Контекст

Пользователь: «агент предлагает решения-костыли не глядя на то что уже настроено и как сделано в среде выполнения».

## Цикл paralysis (7 раундов)

| Раунд | Предложение | Prism-finding | Вердикт |
|-------|-------------|---------------|---------|
| 1 | Добавить правило в MOP | Уже есть правила 10, 13, 14.5 — не срабатывают | Отвергнуто |
| 2 | Создать `prism-reality` скилл | Расширить MOP лучше — это поведение, не анализ | Отвергнуто |
| 3 | Worker/Reviewer разделение (HiClaw) | Reviewer — тот же LLM с тем же stochastic compliance | Отвергнуто |
| 4 | Execution layer hooks (pre-commit, nginx -t) | Static сигнатуры не ловят новое, человек забудет обновить | Отвергнуто |
| 5 | SetFit ML-классификатор | Hermes уже имеет approval system — не подключена к write_file | Отвергнуто |
| 6 | Подключить write_file к approval pipeline | Approval на одном инструменте bypassable через другой | Отвергнуто |
| 7 | Intent-based guard | Агент учится врать в intent'е | Отвергнуто |

## Conservation law

`Coverage × Simplicity = Constant`

Чем сложнее решение, тем выше coverage — но тем ниже вероятность внедрения. 60% решение за день проигрывает 90% решению за неделю, хотя 60% > 0%.

## Симптомы paralysis

1. **Perfect-solution bias** — каждое следующее решение «глубже», но ни одно не внедрено
2. **Progressive escalation** — от строчки в MOP до fanotify мониторинга
3. **Expertise trap** — чем больше знаешь об архитектуре, тем больше failure modes видишь
4. **Zero-vs-perfect fallacy** — текущее состояние (0%) сравнивается с идеальным (100%), а не с практически достижимым (40-60%)

## Правило выхода

«Если я внедрю это сегодня, станет ли система лучше чем сейчас? Если да — делай.»

Тест: внедряем step 1 (простейшее, 40% coverage) за час. Не проектируем step 7 (90% coverage, fanotify, intent validation). Step 7 — после того как step 1 proven working.
