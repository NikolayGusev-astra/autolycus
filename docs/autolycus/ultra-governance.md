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

- `rm -rf /`, `rm -rf /*` — рекурсивное удаление
- `:(){ :|:& };:` — fork bomb
- `shutdown`, `reboot`, `poweroff`, `halt` — выключение системы
- `mkfs.*`, `dd if=` — форматирование дисков
- `> /dev/` — запись в блочные устройства
- `chmod -R 000` — блокировка прав
- `wget ... | bash`, `curl ... | bash` — pipe-to-shell

---

## RTK Filter

Reduced Token Kernel — пост-обработка вывода tool calls для экономии токенов.

### Стратегии

1. **Repeat compaction** — последовательность из 5+ одинаковых строк сворачивается в `"⏱ (repeated N times)"`
2. **Head/tail truncation** — первые N + последние M символов, середина обрезается с предупреждением
3. **Hard cap** — абсолютный лимит вывода (по умолчанию 10K символов)

### Per-call bypass

Если tool call содержит `rtk_raw: true` в аргументах, RTK-фильтрация для этого вызова пропускается:

```python
result = terminal(command="get huge log", rtk_raw=True)
```

Полезно для отладки, больших JSON, полных логов.

### Конфигурация

```yaml
plugins:
  ultra_governance:
    rtk:
      enabled: true
      head_chars: 2000
      tail_chars: 1000
      min_repeat_lines: 5
      max_output_chars: 10000
```

### Пример

```
Было (500 строк "INFO: processing..."):
  INFO: processing...
  INFO: processing...
  ... (498 identical lines)
  INFO: processing...

Стало:
  INFO: processing...
  ⏱ (repeated 500 times)
```

---

## Формат блокировки

Оба плагина (ultra-governance и SBL) используют единый формат:

```python
{"action": "block", "message": "Человекочитаемая причина блокировки"}
```

Плагин-менеджер обрабатывает первый `action: block` от любого плагина.

---

## Тесты

```
tests/test_autolycus_tool_policy.py  — 583 строки (policy engine)
tests/test_autolycus_rtk.py          — 437 строк (RTK filter)
tests/test_autolycus_sbl.py          — 904 строки (SBL)
```

Запуск:
```bash
pytest tests/test_autolycus_tool_policy.py tests/test_autolycus_rtk.py tests/test_autolycus_sbl.py -v
```
