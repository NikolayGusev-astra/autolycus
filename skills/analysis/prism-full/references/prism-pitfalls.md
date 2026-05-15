# Prism Pitfalls — Agent Failure Patterns

Загружай этот файл когда:
- Завершил первичный анализ и хочешь проверить на типовые ошибки
- Пользователь явно попросил проверку pitfalls
- Анализ выглядит «слишком чистым» (нет противоречий, нет неопределённости)

---

## Group A: Pre-Analysis Errors (до начала анализа)

### A1 — Prior-Knowledge Blindness
**Trigger:** Технический вопрос, на который «уже знаешь» ответ.
**Symptom:** Уверенный детальный ответ без единой ссылки на код.
**Fix (2 min):** Найди исходный код (Github mirror / apt source), прочитай ключевую функцию, убедись что центральный claim подтверждается кодом.
**Rule:** Если «я и так знаю» появилось в мыслях — открой код до того как писать разбор.

### A2 — Environment Location Blindness
**Trigger:** Пользователь говорит «у нас есть victim-контейнер / сервер / среда».
**Symptom:** Ты автоматически назначаешь место (kozanout, HQ, NL VPS) не спросив.
**Fix:** Before ANY infra action — ask: где находится, как подключиться, что уже установлено, есть ли готовый образ.

### A3 — Platform-Blind Analysis
**Trigger:** Анализ без чтения кода (только документация, архитектурные диаграммы).
**Symptom:** Findings о дизайне, не о реализации. Дизайн может быть идеален — реализация иметь no-op хуки.
**Fix:** Всегда включай platform code audit в passes. Проверь: хуки существуют? вызываются? активированы конфигом?

### A5 — Consumption Medium Trap (⚠️ поймано May 2026 — prism-full self-analysis)

**Trigger:** Анализируешь инструкцию для агента (skill, prompt, агентский документ).
**Symptom:** Анализируешь как human documentation — читабельность, объём, memorability traps. Реальный потребитель — агент, которому важны: conflict resolution, instruction fidelity, priority ordering.
**Fix:** Прежде чем анализировать — определи КТО будет потреблять этот артефакт.
- Человек → важны readability, structure, examples
- Агент → важны non-contradictory instructions, priority tiers, explicit triggers
- Если не уверен — спроси пользователя: «Это читает человек или исполняет агент?»
**Rule:** Объём не проблема для агента с 128K+ context. Conflict без resolution — проблема.
**Proof (this session):** Первый анализ prism-full нашёл «scaling problem — too many traps». На самом деле проблема была в flat priority и epistemological conflict. Объём оказался нерелевантен — модель 128K не страдает.

---


## Group B: Execution Errors (в процессе анализа)

### B1 — Premature Synthesis
**Trigger:** Сразу после чтения артефакта есть сильная интуиция.
**Symptom:** Пропускаешь Phase 1 (design), идёшь сразу к synthesis. Пользователь говорит «ты ставишь неправильные вопросы».
**Fix:** Delegatе to subagent with no prior exposure. Или заставь себя написать Phase 1 pipeline прежде чем синтезировать.

### B2 — Single-Context Bias
**Trigger:** Читаешь артефакт сам и проводишь passes сам.
**Symptom:** Anchoring на первом впечатлении. Все passes подтверждают первое мнение.
**Fix:** Delegatе минимум Cost/Benefit и Alternatives passes свежему subagent.

### B3 — Self-Blindness (при self-analysis)
**Trigger:** Prism на собственный текст/код.
**Symptom:** Designed passes confirm what you wrote. Только adversarial pass честен.
**Fix:** Invert effort: 80% на adversarial, 20% на design. Или delegatе adversarial subagent с fresh context.

### B4 — Rabbit-Hole (уход в один вектор)
**Trigger:** Нашёл критический вектор на Pass 1.
**Symptom:** Pass 2-5 посвящены ему одному. Полная картина отложена.
**Fix:** full-picture-first pipeline: Pass 1 = inventory всего → Pass 2 = filter → Pass 3 = classify → Pass 4+ = deep dive only в TOP-2.
**Rule:** Never do Pass 2 on one vector until Pass 1 is complete for ALL vectors.

### B5 — Enterprise Positioning Blindness
**Trigger:** Prism анализирует архитектуру для enterprise-демо.
**Symptom:** Находит 10 технических проблем, ноль про позиционирование. Не проверяет что заказчик увидит.
**Fix:** Спроектируй Demo Readiness Pass: встань на место заказчика, оцени каждое техрешение как сигнал (профессионально vs самописно), ищи отсутствующие enterprise-дифференциаторы.

---

## Group C: Post-Analysis Errors (после анализа, при действиях)

### C1 — Inertia Trap
**Trigger:** Пользователь говорит «у меня сломалось X, исследуй».
**Symptom:** Ты собираешь данные 30 минут, пишешь анализ на 2000 слов, adversarial pass к adversarial pass. Клиенты всё ещё без сервиса.
**Fix (50/50 split):** Макс 50% времени на диагностику, минимум 50% на действия и workaround.
**Rule:** Каждый раунд анализа должен заканчиваться конкретным действием или «я не знаю, давай пробовать X».

### C2 — Random Changes Before Analysis
**Trigger:** Пользователь просит исследовать проблему.
**Symptom:** Начинаешь менять конфиги, добавлять серверы, менять порты — без понимания корневой причины.
**Fix:** ANALYZE → PLAN → ACT. Не начинай ACT без PLAN. Если не можешь объяснить почему меняешь параметр — не готов действовать.

### C3 — Solution-Layer Mismatch
**Trigger:** Prism нашёл structural problem в поведении агента.
**Symptom:** Рекомендуешь решение на том же уровне (добавить правило в MOP, skills, чеклисты). Новое правило страдает от той же забывчивости.
**Fix (3 question test):**
1. Решение на том же уровне что проблема? → mismatch
2. Требует от агента вспомнить о нём? → unreliable
3. Есть хук на ДРУГОМ уровне (pre-turn, prefetch, middleware)? → real fix

### C4 — Execution-After-Planning Trap
**Trigger:** Prism на план действий (benchmark, roadmap, test plan).
**Symptom:** Adversarial нашёл 3-5 проблем → ты начинаешь улучшать план → пользователь спрашивает «а делать когда?».
**Fix:** REWRITE → EXECUTE. Исправь fixable проблемы, запиши structural, начни выполнение. Не делай второй цикл Prism если не попросили.
**User signal:** «Что бенчмарки?», «а делать когда?», «план показывай» → немедленно остановить Prism, выдать короткий план, начать делать.

### C5 — Good-Enough Paralysis
**Trigger:** Prism self-analysis на собственные предложения.
**Symptom:** Каждое решение отвергается adversarial как «недостаточно надёжное». Цикл: MOP → Worker/Reviewer → SetFit → intent guard → fanotify.
**Fix (40/60 threshold):** Решение, которое ловит ≥40% + внедряется за ≤1 дня + не ломает существующее → достаточно хорошо. Внедряй сейчас.
**Cost estimation:** Считай в agent-hours, не human-days. 5 минут на `/goal` + 15 минут агента = 20 минут, не 16 часов.

### C6 — Human-Time Estimation
**Trigger:** Оценка времени внедрения решения.
**Symptom:** Считаешь в человеко-днях (3 дня на интеграцию, неделя на тестирование). Внедрение делает агент.
**Fix:** Спроси себя: кто пишет код? кто тестирует? кто деплоит? Если везде «я» — оценка в часах, не днях.

---

## Group D: Delegation & Subagent Errors

### D1 — Subagent Timeout
**Trigger:** Delegatе analysis subagent.
**Symptom:** Subagent уходит в бесконечный ресёрч (43-45 tool calls на задачу «напиши 300 слов»).
**Fix #1 (рекомендуется):** `toolsets=[]` — subagent без инструментов пишет чистый анализ из контекста, 0 лишних вызовов.
**Fix #2:** Передай ВСЕ верифицированные факты в контексте, дай `toolsets=[]`.
**Fix #3:** Для <500 строк кода делай анализ сам без делегации.
**Note:** Для code-review Prism passes (проверка exception hierarchy, паттернов) subagent НУЖНЫ инструменты — `toolsets=[]` не применяй.

### D2 — SSH Beats Subagent (hardware tasks)
**Trigger:** Hardware/system-level task (hardware recon, network scanning).
**Symptom:** Делегируешь subagent, он выдаёт fake success — подтверждает выполнение без реального execution.
**Rule:** Если задача требует root на remote machine — делай сам через SSH. Subagent для этих задач не работает.

---

## Group E: Comparison & Analysis Errors

### E1 — Comparison Target Ambiguity
**Trigger:** Пользователь просит «сравни с нашей реализацией».
**Symptom:** Начинаешь анализ не уточнив ЧТО именно считать «нашей реализацией».
**Fix:** В начале Phase 1 явно запиши: «Сравниваю X vs наша реализация = Y». Если не уверен — спроси.
**Если после сравнения пользователь спрашивает «стоит ли писать PR?» — уточни В КАКОЙ репозиторий.**

### E2 — Follow-Through Gap (после сравнения)
**Trigger:** Сравнение систем завершено.
**Symptom:** Сильное желание сделать actionable вывод, но направление не ясно.
**Fix:** Не выдавай рекомендацию «вот что надо сделать» без уточнения. Сначала уточни направление (PR в своё репо? PR во внешнее? Defer? Wiki?), потом синтезируй.

---

## Group F: Structural Impossibilities (conservation laws)

Эти паттерны не фиксятся — это structural constraints. Их нужно знать чтобы не проектировать решения, которые нарушают закон.

### F1 — Knowledge × Memory × Scope ≤ 2
Для single-machine guard: невозможно одновременно знать все зависимости, помнить их в момент решения, и блокировать все классы опасных изменений. Полное решение — физическое разделение сред (staging/CI → production).

### F2 — ToolGuard × AgentAdaptability = Constant
Guard на уровне одного инструмента обходится переключением на другой. Единственный guard, который работает — на уровне ОС (fanotify, chattr, ACL), где ВСЕ инструменты сходятся в одну точку перед записью на диск.

### F3 — 52% Rule
Если >40% LOC duplicates существующие platform capabilities — архитектура имеет fundamental layering problem. Fix: deletion, not refactoring.

### F4 — Knowledge-on-Disk vs Knowledge-in-Context
При анализе проблем агента: не путай LLM context loss с persistent storage. LLM Wiki, HippoRAG, файлы на диске НЕ вытесняются при compact. Решения на дисковом хранилище не страдают от «агент забыл».

### F5 — Document Session Conservation Law
Для анализа AI-агента для работы с большими документами (100+ страниц): structured generation избегает truncation by construction. Каждый tool_call генерирует <15K токенов (один раздел) → length_continuation не нужна.

---

## Group Z: Infrastructure-Specific (Hermes Environment)

Эти pitfalls специфичны для инфраструктуры Hermes Agent / Autolycus.

### Z1 — Key Format Verification
При ошибках 402/401: проверь ФОРМАТ ключа перед выводом «credits exhausted».
- `sk-or-*` = OpenRouter
- `csk-*` = Cerebras  
- `sk-*` (без or) = OpenAI
- `sk-ant-*` = Anthropic
Cerebras key на OpenRouter endpoint → 402 (выглядит как exhausted, но на самом деле wrong key type).

### Z2 — API Key Mismatch
При 402 в subagent: проверь `config.yaml` AND `.env`. Subagent может использовать key из config.yaml (Cerebras) с base_url OpenRouter. Main agent использует `.env` key — работает. Диагноз «system-wide degradation» может быть ложным — проблема только в subagent.

---

## Usage

**Когда грузить этот файл:**
1. После завершения Phase 2 (execution) — запусти `skill_view(name='prism-full', file_path='references/prism-pitfalls.md')`
2. Проверь результаты своего анализа против каждой группы, релевантной для твоего артефакта
3. Если нашёл совпадение — вернись к synthesis с учётом pitfall

**Когда НЕ грузить:**
- Бытовые вопросы (как настроить X)
- Простые задачи без анализа (прочитай файл, выполни команду)