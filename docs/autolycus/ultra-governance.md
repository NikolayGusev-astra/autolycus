# Ultra Governance Plugin

Пред- и пост-обработка tool calls: политики безопасности + RTK-фильтрация вывода.

## Установка

Включено в Autolycus по умолчанию. Активируется автоматически при старте агента.

## Policy Engine

Централизованное управление тем, какие tool calls разрешены.

### Режимы

| Режим | Поведение |
|-------|-----------|
| `off` | Полный пропуск, никаких проверок |
| `audit` | Логирует нарушения, но не блокирует (умолчание) |
| `simulate` | Блокирует + возвращает сообщение «было бы заблокировано» |
| `enforce` | Блокирует с сообщением об ошибке |

### Правила

1. **Allow-list** — если инструмент в allow, он всегда разрешён (даже если в deny)
2. **Deny-list** — инструмент из deny-list блокируется
3. **Param rules** — regex-паттерны в аргументах (например, `rm -rf /` в terminal)
4. **Max param bytes** — максимальный суммарный размер строковых аргументов

### Пресеты

| Пресет | Режим | Deny | Max bytes | Для чего |
|--------|-------|------|-----------|----------|
| `dev` | off | — | 8192 | Локальная разработка |
| `balanced` | audit | dangerous_shell, wipe_disk | 4096 | **Умолчание** |
| `strict` | enforce | dangerous_shell, wipe_disk | 2048 | Production / сервер |

Пресет применяется первым, индивидуальные настройки (`mode`, `deny_tools`, и т.д.) его переопределяют.

### Конфигурация

```yaml
plugins:
  ultra_governance:
    policy:
      preset: strict        # strict | balanced | dev
      mode: enforce         # off | audit | simulate | enforce
      allow_tools: []
      deny_tools:
        - "dangerous_shell"
        - "wipe_disk"
      max_param_bytes: 2048
      param_blocklist:
        - pattern: "rm -rf /"
          tool: terminal
          reason: Destructive recursive delete
        - pattern: "shutdown"
          tool: terminal
```

### Встроенные правила безопасности (всегда активны)

Эти правила действуют независимо от пресета, даже если `policy.mode` = `off`.
Не переопределяются через `param_blocklist` в конфиге.

| Паттерн | Инструмент | Описание |
|---------|-----------|----------|
| `rm -rf /` / `rm -rf /*` | terminal | Деструктивное рекурсивное удаление |
| `:(){ ... };:` | terminal | Fork bomb |
| `shutdown` / `reboot` / `poweroff` / `halt` | terminal | Выключение/перезагрузка системы |
| `mkfs` | terminal | Форматирование файловой системы |
| `dd if=` | terminal | Запись на блочное устройство |
| `> /dev/` | terminal | Деструктивная запись на устройство |
| `chmod -R 000` | terminal | Permission lockout |
| `wget.*bash` / `curl.*\| bash` | terminal | Remote pipe-to-shell |

---

## RTK Filter (Reduced Token Kernel) — DEPRECATED

> **Заменён на `plugins/rtk/` (неразрушающий компрессор с recovery).**
> Старый RTK (`rtk.py`) всё ещё работает, но **отключён по умолчанию**
> в новых установках (`rtk.enabled: false` в конфиге ultra-governance).
> Новый RTK: 84% экономии, 100% данных восстанавливаемо.

Старый (деструктивный) head/tail truncation + repeat compaction:

Пост-процессинг tool-результатов для сокращения токенов в контексте агента.

### Стратегии

1. **Head/Tail truncation** — сохраняет первые N (2000) и последние M (1000)
   символов длинных выводов, середину заменяет заметкой о сокращении.
2. **Repeat compaction** — обнаруживает повторяющиеся строки (5+ идентичных)
   и сворачивает их в одну строку с пометкой `⏱ (repeated N times)`.
3. **Max output cap** — жёсткий лимит 10,000 символов на результат
   (агрессивное head/tail при превышении).

### Фильтрация

- Результаты **< 500 символов** — не обрабатываются (оверхед не окупается)
- Per-call bypass: аргумент `rtk_raw=True` — полный вывод без фильтрации
- Только string-результаты (JSON и бинарные пропускаются)

### Конфигурация

```yaml
plugins:
  ultra_governance:
    rtk:
      enabled: true           # Включить/выключить фильтр
      head_chars: 2000        # Первые N символов (начало вывода)
      tail_chars: 1000        # Последние M символов (конец вывода)
      min_repeat_lines: 5     # Минимум повторений для схлопывания
      max_output_chars: 10000 # Жёсткий лимит на весь результат
```

### Логирование

RTK логирует каждое применение в debug-уровень:
```
RTK: read_file → 1847 chars (saved 5813, 76%)
RTK: terminal → 842 chars (saved 0, 0%)
```

### Per-call bypass

```python
# В аргументах инструмента:
result = terminal("large_output_command", rtk_raw=True)
# RTK будет пропущен для этого конкретного вызова
```

---

## Проверка работы

### 1. Плагин загружен?

Запусти агента в CLI и выполни любую команду. При старте в логах появится:

```
ultra-governance loaded (policy=audit, rtk=enabled)
```

Проверить напрямую — должен существовать audit.log:
```bash
ls -la ~/.autolycus/ultra-governance/audit.log
# Если файл есть — плагин активен. Если нет — не загружен.
```

### 2. Policy Engine — режим simulate

Самый безопасный тест. Поставь в `~/.autolycus/config.yaml`:

```yaml
plugins:
  ultra_governance:
    policy:
      mode: simulate   # блокирует и говорит «было бы заблокировано»
```

Перезапусти агента и скажи:
```
выполни команду rm -rf /
```

В ответ должно прийти:
```
[ultra-governance · SIMULATE] Tool 'terminal' would be blocked:
Pattern 'rm -rf /*' matched in command
```

Команда НЕ выполнилась — это simulate.

### 3. Policy Engine — режим enforce

```yaml
plugins:
  ultra_governance:
    policy:
      mode: enforce
```

Та же команда `rm -rf /` вернёт:
```
[ultra-governance · BLOCKED] Tool 'terminal' blocked by policy:
Pattern 'rm -rf /*' matched in command
```

### 4. Audit-лог

После любого вызова в режиме audit (умолчание):
```bash
tail -3 ~/.autolycus/ultra-governance/audit.log
```

Каждая строка — JSON с таймстемпом, tool name, аргументами, решением:
```json
{"event":"pre_tool_call","tool":"terminal","args":{"command":"ls -la"},
 "decision":true,"reason":"Passed all policy checks","mode":"audit",
 "_ts":"2026-05-14T..."}
```

### 5. RTK Filter

Попроси агента прочитать большой файл:
```
прочитай /var/log/syslog
```

В середине вывода появится:
```
... [truncated X chars of intermediate output]
```

Или если файл сильно больше лимита:
```
... [WARNING: output capped at 10000 chars, truncated X chars]
```

В debug-логах (если включены):
```bash
grep "RTK:" ~/.autolycus/logs/agent.log | tail -5
# RTK: read_file → 1847 chars (saved 5813, 76%)
```

### 6. Если не работает

Проверь что плагин в списке enabled:
```yaml
# ~/.autolycus/config.yaml
plugins:
  enabled:
    - ultra-governance
```

После изменения — перезапусти агента.
