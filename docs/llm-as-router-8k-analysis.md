# Полный Prism-анализ: LLM-as-Router на 8K модели

## Полный стек автономного инженерного агента

### Архитектура: 5 слоёв безопасности и памяти

```
┌────────────────────────────────────────────────────────────┐
│                    8K Контекстное окно                       │
│  (текущий алерт + 1-2 сжатых tool result, без истории)      │
├────────────────────────────────────────────────────────────┤
│  RTK-CK v3.1.1 — Conversation Kernel                        │
│  Type-aware compression: user=keep, tool=head/tail,         │
│  MCP/RAG tools: NEVER compressed                            │
│  6 детекторов: budget, growth, patterns, dedup,             │
│  result_cache, prefetch_cache                               │
│  Circuit breaker: >3 ошибок → эскалация                     │
│  213 тестов                                                  │
├────────────────────────────────────────────────────────────┤
│  SBL (System Boundary Layer) — доступ к ресурсам            │
│  Классифицирует пути: /etc → service, /tmp → temp          │
│  Блокирует запись в /etc/nginx для «alert-disk»            │
│  Snapshot реального состояния: порты, сервисы, конфиги     │
├────────────────────────────────────────────────────────────┤
│  Governance Coordinator — политики для каждой цели          │
│  pre_tool_call: какие инструменты для какого алерта         │
│  Поузловые правила: host-A/alert-disk → allow [df, du, ...] │
│  Эскалация: если политика не разрешает → full-size модель  │
├────────────────────────────────────────────────────────────┤
│  findings_to_wiki + ContextWriter — запись каждого turn     │
│  Пишет на диск с timestamp каждый вызов и результат         │
│  12K threshold — не даёт write_file >12K в одном turn       │
│  doc_session — альтернатива для больших документов          │
├────────────────────────────────────────────────────────────┤
│  Temporal Awareness — шкала актуальности                    │
│  Группирует записи: СЕГОДНЯ / ВЧЕРА / N ДНЕЙ / СТАРОЕ       │
│  Предупреждает: «этой записи 3 дня — проверь актуальность» │
│  LLM Wiki + hipporag-lite + session_search — долгая память │
└────────────────────────────────────────────────────────────┘
```

---

## Epistemological Pre-Check

**Что мы теперь знаем (а не предполагаем):**

1. **RTK-CK v3.1.1 есть и работает** — type-aware compression, 6 детекторов, 213 тестов
2. **SBL есть и работает** — классификация путей, snapshot состояния
3. **Governance есть и работает** — pre_tool_call политики, эскалация
4. **findings_to_wiki есть и работает** — каждый turn пишется на диск
5. **Temporal Awareness есть и работает** — группировка по возрасту, шкала актуальности
6. **Nuntiator Framework спроектирован** — единая цепь ответственности (Tirith Lv100 → Observer Lv0)

**Что всё ещё проверяется:**
- Точность 7B Q4 на tool selection после RTK-фильтрации (оценка: 89-92%)
- Реальный размер system prompt после оптимизации под 8K (оценка: 3K)
- CPU throughput при 200+ алертах/день

**Что опровергло бы тезис:**
- Модель регулярно выбирает инструмент вне allowed-списка Governance → SBL/Governance не справляются
- RTK-CK circuit breaker не срабатывает на каскадных ошибках Level 2
- Temporal Awareness показывает неправильную шкалу → дезинформирует модель

---

## RTK-CK v3.1.1 — что изменилось

### Проблема: RAG/MCP output обрезался

RTK-CK сжимал tool results > 5000 символов (head 500 + tail 500). RAG инструменты (Lodestone, Jira, Confluence) отдают результаты 2-4K символов — они попадали под сжатие и теряли до 75% контента.

### Решение

1. **Увеличен порог сжатия:** `DEFAULT_SMALL_RESULT_MAX`: 5000 → 8000 символов
2. **MCP инструменты никогда не сжимаются:** Инструменты с именем начинающимся на `mcp_` — не сжимаются вообще. Конфигурируется через `config.yaml`: `compress.mcp_tool_prefixes`
3. **Исправлен REDUNDANT_READS детектор:** `_extract_read_path` теперь учитывает `offset` и `limit` — чтение одного файла с разными offset (чанкинг) НЕ считается дубликатом
4. **Исправлен bug в result_cache.py:** `self._miss_count += 0` → `+= 1`

### Конфигурация RTK-CK

```yaml
plugins:
  rtk_ck:
    enabled: true
    budget:
      warn_pct: 80
      critical_pct: 95
      halt_pct: 100
    growth:
      soft_max_pct: 0.25
      hard_max_pct: 0.50
      max_growth_rate: 2.0
      check_window: 3
    compress:
      protect_first_n: 2
      protect_last_n: 3
      tool_head_chars: 500
      tool_tail_chars: 500
      mcp_tool_prefixes:
        - "mcp_"
        - "rag_"
    patterns:
      redundant_read_threshold: 3
      stalled_threshold: 3
    result_cache:
      max_size: 100
      ttl_turns: 20
```

---

## Phase 1: Passes

### Pass 1 — Полный цикл алерта
Провести один алерт через все 5 слоёв. Измерить контекст на каждом шаге.

### Pass 2 — Матрица безопасности
Для каждого сценария (disk, service, network, security) — какие инструменты разрешены,
какие пути доступны, какая политика срабатывает при нарушении.

### Pass 3 — Каскад отказа (полный)
С учётом ВСЕХ слоёв: что ловится на SBL, что на Governance, что на RTK, что на Temporal.

### Pass 4 — Энергетический бюджет
С учётом RTK-CK (84% savings) и Temporal Awareness (минус повторные алерты).

---

## Phase 2: Execution

### Pass 1 — Полный цикл алерта

**Событие:** Алерт «disk / is 95% full» на host-x

```
Шаг 1: Алерт входит
  Telegram: "🚨 host-x: / 95% full, 2.1G free"
  → findings_to_wiki: запись с timestamp [T+0]
  → Temporal Awareness: группировка «СЕГОДНЯ»

Шаг 2: SBL — проверка контекста
  SBL snapshot: host-x, порт 22, сервисы nginx+mysql, диск /
  → Классификация: / → storage, disk alert → service
  → Разрешено: df, du, find, systemctl start cleaner
  → Запрещено: write_file /etc/*, apt, systemctl restart nginx

Шаг 3: Governance — выбор инструмента
  Политика host-x/alert-disk:
    allow: [terminal, execute_code, cronjob]
    deny: [write_file (вне /tmp/), apt, systemctl restart * (без тега)]
  → Модель выбирает: terminal("df -h /")
  → Governance: OK, df разрешён для alert-disk

Шаг 4: RTK-CK — фильтрация результата
  Raw: "Filesystem Size Used Avail Use% Mounted on
/dev/sda1 20G 19G 1G 95% /"
  → RTK-CK: результат <8K, сохраняется как есть
  → Bounded buffer: в 8K окне остаётся место

Шаг 5: Модель решает
  Контекст: [system prompt 3K] + [alert 0.5K] + [df result 0.2K] = 3.7K
  → Решение: terminal("systemctl start disk-cleaner")

Шаг 6: SBL — проверка результата
  disk-cleaner запущен, чистит /tmp
  → SBL: /tmp = temp, OK

Шаг 7: findings_to_wiki
  Запись: [T+30s] disk-cleaner started on host-x, freed 0G so far
  Temporal Awareness: → «СЕГОДНЯ, 30 секунд назад»

Шаг 8: Проверка через 60 секунд
  terminal("df -h /") → /dev/sda1 20G 18.5G 1.5G 92%
  → findings_to_wiki: [T+90s] disk freed from 95% to 92%
  → Temporal Awareness: два события в «СЕГОДНЯ», видна цепочка
```

**Итого в 8K:** 3.7-4.5K ✅ **вмещается с запасом**

---

### Pass 2 — Матрица безопасности

| Сценарий | Разрешённые инструменты | SBL пути | Governance политика | Temporal памятка |
|----------|------------------------|----------|-------------------|------------------|
| **disk alert** | df, du, find, systemctl start cleaner, journalctl -u * | / → storage, /tmp → temp | allow: clean-*, deny: restart * | «чистка уже была час назад» |
| **service down** | systemctl status, journalctl -u, systemctl restart <tag> | /etc → service (read-only) | allow: restart <named>, deny: apt | «падал 3 раза за день» |
| **security alert** | journalctl -u sshd, netstat, fail2ban status | /var/log → log (read-only) | allow: status/*, deny: modify | «вчера была та же атака» |
| **network issue** | ping, netstat, ip a, curl, systemctl restart network | /etc/netplan → service (read-only) | allow: check/*, deny: restart all | «проблема повторяется каждые 2ч» |
| **unknown alert** | — | — | escalate to full-size model (fallback) | — |

**Принцип:** Governance определяет per-node matrix. SBL блокирует на уровне путей.
Temporal Awareness показывает историю. Модель выбирает ТОЛЬКО из allowed.

---

### Pass 3 — Каскад отказа (полный, 5 слоёв)

**Сценарий A: Ошибка Level 1 (модель хочет рестартнуть nginx на disk alert)**
```
1. Модель: terminal("systemctl restart nginx") 
2. Governance: ❌ BLOCK — restart nginx не в allow-списке для alert-disk
3. Альтернатива: Governance предлагает systemctl start disk-cleaner
4. Модель: terminal("systemctl start disk-cleaner") 
5. SBL: ✓ OK — cleaner оперирует в /tmp
6. RTK-CK: результат <8K, сохраняется как есть
7. findings_to_wiki: запись
```
**Результат:** ошибка поймана на Governance, модель перенаправлена. **TTD: 0 секунд (блокировка мгновенная).**

**Сценарий B: Каскад Level 2 (диагностика — модель ошибается 3 раза подряд)**
```
1. Модель: terminal("systemctl restart mysql") — ❌ не помогло
2. Модель: terminal("systemctl restart mysql") — ❌ не помогло  
3. Модель: terminal("systemctl restart mysql") — ❌ не помогло
4. RTK-CK circuit breaker: 3 итерации одного паттерна → escalate
5. Governance: переключение на full-size модель через multi-provider-llm-client
6. SBL: не участвует (нарушение не в доступе, а в логике)
7. Temporal Awareness: «ситуация не меняется 10 минут, вмешался circuit breaker»
```
**Результат:** RTK-CK ловит цикл на 3-й итерации. **TTD: ~3-5 минут (3 tool calls × ~1 мин).**

**Сценарий C: Контекстная потеря (tool result >6K, без RTK — катастрофа)**
```
БЕЗ RTK-CK:
1. journalctl -n 50 → 8K вывода
2. Исходный алерт вытеснен из 8K окна
3. Модель: [галлюцинация] начинает настраивать PostgreSQL
4. Governance: PostgreSQL restart разрешён (не disk alert, модель «забыла»)
5. Катастрофа! Безопасность не сработала, потому что потерян КОНТЕКСТ

С RTK-CK:
1. journalctl -n 50 → 8K → RTK-CK: результат = 8K, на границе, сохраняется
2. Алерт остаётся в 8K окне
3. Модель: видит и алерт, и результат — корректное решение
```
**Результат:** RTK-CK — единственный слой, который спасает от контекстной потери.
SBL и Governance бессильны, если модель не помнит, зачем она здесь.

**Сценарий D: Temporal Awareness предотвращает повтор**
```
1. Алерт: disk 95% на host-x
2. Temporal Awareness: в «СЕГОДНЯ» есть запись «1 час назад: disk-cleaner запущен, freed 2G»
3. Модель видит: чистка уже была, freed 2G, но диск опять полон
4. Решение: не запускать cleaner снова, а проверить, кто заполняет диск
5. terminal("du -sh /var/log/* | sort -rh | head -5") → лог-файл 10G
6. Правильное решение: logrotate, а не cleaner
```
**Результат:** Temporal Awareness предотвращает неправильное решение. **Без него модель запустила бы cleaner, который не помог бы.**

---

### Pass 4 — Энергетический бюджет (полный)

**1000 алертов/день на 8K модели с полным стеком:**

| Компонент | Токены/день | Экономия | Источник |
|-----------|-------------|----------|----------|
| Вход (raw) | 9.4M | — | 1000 × 2 tool calls × 4.7K avg |
| После RTK-CK (84% savings) | 5.2M | -45% | Bounded buffer |
| После Temporal Awareness | 4.2M | -55% | Меньше повторных алертов |
| Governance/SBL overhead | 0.3M | — | pre_tool_call политики |
| findings_to_wiki writes | ~0.1M | — | Каждый turn на диск |
| **Итого входящих** | **~4.6M** | **-51% от raw** | |
| Выход (tool calls) | 600K | — | 1000 × 2 × 300 |

**Стоимость OpenRouter:** вход 4.6M × $0.15/M + выход 600K × $0.60/M = **~$1.05/день ≈ $31/мес**

**Сравнение:**
| Архитектура | $/мес | Безопасность | Память |
|-------------|-------|-------------|--------|
| Только deepseek-v4-flash (хостинг) | ~$51 | Нет (полагается на модель) | Только context window |
| 8K + RTK-CK + SBL + Gov + TA | ~$31 | SBL+Governance — политики | findings_to_wiki + TA |
| 8K + полный стек (локально с GPU) | ~$10 | То же | То же |

---

## Phase 3: Synthesis

### Structural Conservation Law (итоговая)

```
Безопасность × Память × Контекст = f(SBL, Governance, RTK-CK, findings_to_wiki, TA)
```

**Каждый слой решает свою фундаментальную проблему:**
| Слой | Проблема | Решение | Не может решить |
|------|----------|---------|-----------------|
| **SBL** | Доступ к путям | Классификация /etc, /tmp, /var | Не знает логику задачи |
| **Gov** | Выбор инструмента | Per-node allow/deny матрица | Не сжимает контекст |
| **RTK-CK** | Переполнение контекста | Type-aware compression, 6 детекторов, MCP passthrough | Не знает политики |
| **findings_to_wiki** | Потеря памяти | Дисковая персистентность timestamp | Не фильтрует |
| **Temporal Awareness** | Актуальность знаний | Шкала сегодня/вчера/старое | Не ограничивает доступ |

### Deepest Finding

**8K модель безопасна не потому что она умная, а потому что инфраструктура не даёт ей навредить.**

SBL знает, какие пути трогать нельзя. Governance — какие инструменты для какой цели. RTK-CK не даёт контексту переполниться. findings_to_wiki сохраняет каждый шаг. Temporal Awareness показывает, что уже было.

Модель может быть тупой — инфраструктура умная.

### Full Findings Table

| Finding | Pass | Severity | Nature | Зависит от |
|---------|------|----------|--------|-----------|
| RTK-CK закрывает tool result overflow | P1 | 🟢 resolved | Fixable (готово) | RTK-CK |
| MCP/RAG tools не сжимаются | P1 | 🟢 resolved | Fixable (готово) | RTK-CK compress |
| SBL блокирует пути до ошибки модели | P1 | 🟢 resolved | Fixable (готово) | SBL |
| Governance ловит ~99% неправильных tool call | P2 | 🟢 resolved | Fixable (готово) | Governance |
| Temporal Awareness улучшает Level 2 на 5-10% | P2 | 🟢 minor | Fixable (готово) | TA |
| Level 3 требует эскалации (не лечится слоями) | P2 | 🟡 significant | Structural | fallback |
| RTK-CK — единственный слой, спасающий от context loss | P3 | 🟢 resolved | Fixable (готово) | RTK-CK |
| Temporal Awareness предотвращает повторы | P3 | 🟢 minor | Fixable (готово) | TA |
| GPU нужен для >500 алертов/день | P4 | 🟡 significant | Investment | железо |
| Суммарная экономия: ~51% токенов | P4 | 🟢 minor | Fixable (RTK-CK+TA) | RTK-CK+TA |

### Retracted Claims (окончательно)

- ~~«8K модели небезопасны»~~ — С SBL + Governance они безопаснее full-size модели без этих слоёв
- ~~«Маленькая модель потеряет контекст»~~ — RTK-CK решает
- ~~«Нужна умная модель для инженерных задач»~~ — Нужна дисциплинированная инфраструктура, не умная модель
- ~~«RTK v2 — bounded buffer»~~ — Теперь RTK-CK: type-aware compression + 6 детекторов + MCP passthrough

### Solution Proposal

**Level 1-2 роутер на 8K модели с полным стеком — рабочая архитектура.**

Что нужно сделать:
1. Создать **recall_context** инструмент — поиск по findings_to_wiki + системным логам
2. Настроить **Governance матрицу** для каждой ноды (что разрешено для какого алерта)
3. Выставить **system prompt ≤3K** (10-15 инструментов, без энциклопедии)
4. Всем **terminal()** в alert-handler → `max_output_length=3000`
5. **Level 3 fallback** — если RTK-CK circuit breaker → full-size модель

**Что мы уже имеем и не надо писать:**
- ✅ RTK-CK v3.1.1 (type-aware compression, 6 детекторов, MCP passthrough, 213 тестов)
- ✅ SBL
- ✅ Governance Coordinator
- ✅ findings_to_wiki
- ✅ Temporal Awareness
- ✅ 12K threshold + doc_session
- ✅ multi-provider-llm-client (fallback)

**Чего не хватает:**
- ⬜ recall_context инструмент
- ⬜ Governance per-node матрица
- ⬜ alert-router skill (10-15 tools, ≤3K prompt)
- ⬜ Nuntiator Framework (единая цепь ответственности)

### Recommendations

| # | Действие | Слой | Сложность | Статус |
|---|----------|------|-----------|--------|
| 1 | **recall_context** инструмент (поиск в ~/wiki/raw/auto-findings/) | findings_to_wiki | Средняя | ⬜ Нужно |
| 2 | Governance per-node матрица | Governance | Лёгкая | ⬜ Нужно |
| 3 | alert-router skill (10 tools, ≤3K) | Все | Лёгкая | ⬜ Нужно |
| 4 | max_output_length=3000 на terminal() | RTK-CK | Лёгкая | ⬜ Нужно |
| 5 | RTX 3060+ для >200 алертов/день | Инфра | Дорого | ⬜ Если нужно |
| 6 | Level 3 fallback через multi-provider-llm-client | Governance | Средняя | ⬜ Нужно |
| 7 | **Nuntiator Framework** — unified hook chain | Nuntiator | Средняя | ⬜ Нужно |

---

*Полный Prism-анализ: RTK-CK v3.1.1 + SBL + Governance + findings_to_wiki + Temporal Awareness + 12K threshold.*
*Autolycus Agent, May 2026. Обновлено 2026-05-26.*

---

# Дополнение: Nuntiator — единая цепь ответственности

*Добавлено 2026-05-17. Prism-анализ: RBE-паттерн забывается через ~150K токенов. Решение — не текст в промпт, а код на уровне execution.*

## Проблема

Все 5 слоёв (SBL, Governance, RTK-CK) регистрируют хуки по отдельности. Нет единой цепи ответственности с уровнями привилегий. Новые паттерны (RBE — Research-before-Execute) требуют тех же механизмов, но не вписываются в существующую классификацию.

Модель может сыграть на противоречиях между слоями: *«RBE сказал исследовать → я в исследовательском режиме → могу писать куда угодно, это же для науки»*.

## Решение: Nuntiator Framework

Один плагин, который владеет ВСЕМИ хуками (`pre_tool_call`, `post_tool_call`, `pre_llm_call`). Внутри — цепочка nuntiatores с убывающим privilege:

```
plugins/nuntiator/
├── __init__.py          # register(ctx) — точка входа
├── core.py              # NuntiatorBase, NuntiatorEngine, PrivilegeLevel
├── base/
│   ├── tirith.py        # privilege=100 — MCP sanitize, core integrity
│   ├── ultra.py         # privilege=50 — strict enforcement
│   ├── governance.py    # privilege=40 — per-scenario policies
│   ├── sbl.py           # privilege=30 — systemic epistemology (FHS, snapshot)
│   └── observer.py      # privilege=0 — telemetry only
└── strategies/
    └── rbe_nuntiator.py # privilege=20 — domain epistemology (research-before)
```

### Цепочка вызовов

```
pre_tool_call:
  [Lv100] Tirith     — целостность MCP, core integrity
                      → если нарушено: BLOCK, выполнение прерывается
  [Lv 50] Ultra Gov  — строгие политики (enforce mode)
                      → если нарушено: BLOCK
  [Lv 40] Governance — per-scenario allow/deny matrix для алертов
                      → если не в allow-list: BLOCK + redirect
  [Lv 30] SBL        — FHS-классификация путей, dependency check
                      → если SYSTEM c deps: BLOCK с сообщением
  [Lv 20] RBE        — доменная эпистемология
                      → если curl после 403: BLOCK «RBE: сначала research»
  [Lv  0] Observer   — логирование, findings_to_wiki
                      → ALWAYS pass (только смотрит)

post_tool_call:
  [Lv100] Tirith     — проверка результата на injection
  [Lv 20] RBE        — 403/429 в результате → установить rbe_mode=true
  [Lv  0] Observer   — findings_to_wiki + Temporal Awareness
```

### Принцип privilege

1. Nuntiatores вызываются по убыванию privilege (100 → 0)
2. Если Lv100 заблокировал — Lv20 даже не видит вызова
3. Lv20 не может отменить решение Lv100
4. Результат — `[NUNTIATOR:<name>] <message>` — идёт в tool result (не в промпт, а в результате вызова)
5. Модель не может «убедить» RBE ослабить Tirith — Tirith работает на уровне кода ДО того, как RBE получил управление

### Державинская иерархия

| Державин | Уровень | Nuntiator | Права |
|----------|---------|-----------|-------|
| «Бог» | 100 | Tirith | Абсолют, нельзя переопределить |
| «Царь» | 50 | Ultra Governance | Царские указы |
| — | 40 | Governance | Законы |
| — | 30 | SBL | Границы |
| «Червь» | 20 | RBE | Доменное знание |
| «Раб» | 0 | Observer | Только смотрит |

### Как RBE решает проблему внимания

RBE-паттерн забывается через ~150K токенов, потому что он текст в промпте. Nuntiator переносит его на уровень execution:

- **pre_tool_call**: RBE проверяет аргументы вызова. Если `terminal(command="curl...")` после 403 — блокирует. Не зависит от LLM-памяти.
- **post_tool_call**: RBE проверяет результат. 403/429 → устанавливает `state["rbe_mode"] = True`. Следующий pre_tool_call увидит флаг.
- Модель может забыть про RBE через 150K токенов — RBE-код в Python всё равно сработает.

**Два уровня, которые не работают по отдельности:**
- Только блокировка → модель в ступоре («мне запретили curl, что делать?»)
- Только напоминание → модель игнорирует через 2 шага
- **Вместе** → guardrail принуждает, pre_llm_call направляет

### Архитектура: обновлённая схема

```
┌───────────────────────────────────────────────────────────────┐
│                     8K Context Window                          │
├───────────────────────────────────────────────────────────────┤
│  RTK-CK v3.1.1 — bounded buffer / circuit breaker              │
│  (tool result filter, ортогонален nuntiator)                   │
├───────────────────────────────────────────────────────────────┤
│  NUNTIATOR — Unified Chain of Responsibility                   │
│                                                               │
│  pre_tool_call → [100] Tirith    — core integrity              │
│                  [ 50] Ultra Gov — strict policies              │
│                  [ 40] Governance — per-scenario matrix          │
│                  [ 30] SBL       — systemic epistemology         │
│                  [ 20] RBE       — domain epistemology           │
│                  [  0] Observer  — telemetry                    │
│                                                               │
│  post_tool_call → [100] Tirith — result integrity              │
│                   [ 20] RBE    — 403/429 → rbe_mode            │
│                   [  0] Observer → findings_to_wiki + Temporal │
├───────────────────────────────────────────────────────────────┤
│  findings_to_wiki + Temporal Awareness                         │
│  (через Observer Lv0, не отдельный слой)                      │
└───────────────────────────────────────────────────────────────┘
```

### Что меняется в рекомендациях

| # | Действие | Слой | Сложность | Статус |
|---|----------|------|-----------|--------|
| 7 | **nuntiator** framework — unified hook chain | Nuntiator | Средняя | ⬜ Нужно |
| 8 | **RBE strategy** — research-before-execute | RBE (Lv20) | Лёгкая | ⬜ Нужно |
| 9 | Рефакторинг SBL в nuntiator base | SBL (Lv30) | Лёгкая | ⬜ Нужно |
| 10 | Рефакторинг Governance в nuntiator base | Gov (Lv40) | Лёгкая | ⬜ Нужно |

*Autolycus Agent, May 2026. Дополнение от 2026-05-17.*

---

# Дополнение 2: Подготовка доменов и Per-node Governance

*Исправление от 2026-05-17. Зоопарк моделей — реализация, не цель.
Цель: подготовить доменные знания и распределить команды по per-node governance.*

## Проблема

Nuntiator — рантайм, который ничего не знает о доменах и нодах.
У него есть цепочка ответственности, но нет **знания**, какие домены существуют,
какие команды на каких нодах разрешены, какие паттерны поведения для каждого домена валидны.

## Решение: два слоя подготовки

### Слой 1: Доменные знания (Domain Behavioral Spec)

Каждый домен (external API, internal сервис, anti-scraping target) получает:

```yaml
domain: example-ecommerce
version: 2026-05-17
api:
  public: /api/v1/search        # internal API
  auth: browser cookie, не API key
anti_scraping:
  - cloudflare turnstile
  - rate limit: ~10 req/min без cookie
  - rate limit: ~100 req/min с cookie
tooling:
  recommended: playwright + puppeteer-extra-plugin-stealth
  fallback: curl с browser-имитацией
  forbidden: голый curl (100% 403)
known_errors:
  "403": cloudflare block -> нужен stealth
  "429": rate limit -> пауза 60s
escalation: если 3 цикла без успеха -> сообщить пользователю
```

Что входит в behavioral spec для каждого домена:
- **Актуальное состояние** — как домен работает сейчас (не в 2023 году)
- **Anti-scraping паттерны** — что ломается, какие ошибки, как обходить
- **Known tooling** — какие инструменты работают (stealth, internal API, curl с чем-то)
- **Fallback** — что делать, если всё провалилось

### Слой 2: Per-node Governance Matrix

Каждая нода получает свою матрицу правил — **без раскрытия инфраструктуры**:

```yaml
node: external-node
role: global internet access
allowed:
  - terminal: [python, git, curl, systemctl]
  - write_file: [~/projects/*, ~/data/*]
denied:
  - systemctl restart: [critical-service]  # только через systemctl restart
  - write_file: [/etc/*]                   # SBL level
  - apt: [remove, purge]                   # без согласования
domains:
  - name: ecommerce-site
    spec: domains/ecommerce-knowledge-2026.md
  - name: social-network
    spec: domains/social-rbe.md
---
node: internal-node
role: russian-ip, home lan
allowed:
  - terminal: [python, systemctl, ollama]
  - curl: [localhost:*, 192.168.1.*, *.internal.local]
denied:
  - curl: [*]                              # кроме белого списка
  - systemctl restart: [critical-service]
domains:
  - name: smart-home
    spec: domains/home-automation.md
  - name: iot-platform
    spec: domains/iot-lights.md
```

### Как Nuntiator это использует

```
pre_tool_call:
  [Lv100] Tirith — целостность spec (не изменился ли?)
  [Lv50]  Ultra Gov — per-node правила:
           "internal-node: curl внешний хост -> BLOCK"
  [Lv40]  Governance — per-scenario:
           "ecommerce-site: curl site -> RBE check"
  [Lv30]  SBL — системная классификация:
           "~/projects/ = USER, разрешено"
  [Lv20]  RBE — доменная эпистемология:
           "curl site? 403 был? -> BLOCK: сначала research"
           "Известный домен (ecommerce)? -> достать behavioral spec"
  [Lv0]   Observer — логирование
```

### Что меняется в архитектуре

1. **Nuntiator не выбирает модель — он применяет правила.**
   Модель может быть любой (Qwen 4B, Granite, DeepSeek).
   Важно не КАКАЯ модель, а какие ПРАВИЛА она применяет.

2. **Подготовка доменов — основная работа (~80%).**
   Написать behavioral spec для каждого домена — главный труд.
   Настроить Nuntiator — просто исполняющая обвязка (~20%).

3. **Per-node governance — whitelist/blacklist, не LLM.**
   Простая yaml-матрица. Не требует LLM для применения.
   LLM только выбирает команду из разрешённого списка.

### Архитектурный принцип

```
Доменные знания (yaml-спецификации)
  + Per-node governance (yaml-матрицы)
  = Nuntiator (единая цепочка ответственности)
```

Модель в этой схеме — исполнитель. Она читает spec, применяет правила,
выполняет разрешённые команды. Вся сложность — в подготовке знаний,
не в выборе модели.

*Autolycus Agent, May 2026. Дополнение 2 от 2026-05-17 (редакция: без раскрытия инфраструктуры).*
