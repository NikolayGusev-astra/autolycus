# Existing-Infrastructure Blindness — Case Study (May 2026)

## Контекст

Пользователь (владелец инфраструктуры) описывает проблему: агент предлагает решения, не глядя на то, что уже настроено в среде выполнения. Агент «костылит», не зная как выпускаются сертификаты, как настроена почта, какие сайты хостятся.

## Цепочка неверных предложений агента

### Предложение 1: Grounding snapshot в prefetch memory provider
**Что предложил:** Добавить в findings_to_wiki prefetch блок, который перед каждым turn'ом собирает snapshot среды (systemctl, docker ps, ss -tlnp, git log).

**Почему не сработало:**
- Snapshot видит части (порты, сервисы), но не связи (5 сайтов на одном 443, общий upstream для certbot)
- 300-600 токенов создают false confidence — агент «проверил» и уверен, но не знает архитектуры
- Невозможно загрузить всю архитектуру в контекст

**Пользователь поправил:** «сбор слепка не расскажет агенту как мы выпускаем сертификаты, как настроена почта, какие сайты у нас хостятся»

### Предложение 2: Worker → Reviewer sub-agent делегирование
**Что предложил:** Разделить агента на Worker (предлагает) и Reviewer (проверяет diff через system-map.yaml).

**Почему не сработало:**
- Reviewer — такой же LLM с тем же stochastic compliance
- Разделение контекста лечит загрязнение, не лечение применения
- Sub-agent — self-report, не гарантия исполнения

**Пользователь поправил:** «агент не вспомнил директиву — проблема на том же уровне»

### Предложение 3: SetFit-классификатор как execution layer guard
**Что предложил:** Обучить binary classifier (diff → опасно/безопасно), повесить как pre-write hook.

**Почему не сработало:**
- В Hermes **уже есть** approval system (tools/approval.py) с HARDLINE_PATTERNS, DANGEROUS_PATTERNS, Smart approval LLM, YOLO-mode
- Проблема не в отсутствии guard'а, а в том что write_file/patch не подключены к approval pipeline
- Достаточно было подключить существующий approval к file_tools.py

**Пользователь поправил:** «в hermes agent и так по умолчанию есть approval, он на cat nginx.conf запросит а патчит сам в рамках задачи»

## Глубинная структурная проблема

**Existing-infrastructure blindness:** когда агент слышит описание проблемы, он входит в режим проектирования, не сделав предварительный аудит существующего кода. Предлагает новые слои, не зная что нужный слой уже есть.

## Conservation law

**DesignNovelty × ExistingCodeAudit = Constant.** Чем быстрее агент переходит к проектированию нового решения, тем меньше он проверил существующий код. И наоборот: чем глубже аудит, тем меньше «новых» решений требуется — чаще всего нужно донастроить/подключить то, что уже есть.

## Фикс

Before proposing ANY architectural solution to a codebase the agent has access to:
1. `search_files()` по 3-5 ключевым словам проблемы
2. Read the actual source code (not docs, not README)
3. Ask: "does 80% of this already exist but is just not wired/configured?"
4. Only then design new components
