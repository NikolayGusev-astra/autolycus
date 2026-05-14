# ПМИ — SBL (System Boundary Layer)

> Программа и Методика Испытаний для SBL-модуля Autolycus Agent.
> Все тесты выполняются в CLI Autolycus на любой системе (ноутбук, VPS).

## 1. Объект испытаний

**SBL (plugins/sbl/)** — детерминированный FHS-классификатор на уровне
tool call dispatch. Перехватывает write-инструменты, классифицирует пути
(SYSTEM/USER/UNKNOWN), снимает снапшот инфраструктуры и проверяет
зависимости перед записью в системные пути.

## 2. Условия испытаний

- Autolycus Agent v0.1+ установлен локально
- В `~/.autolycus/config.yaml` в `plugins.enabled` есть `sbl`

  ```yaml
  plugins:
    enabled:
      - sbl
  ```

- Агент запущен в CLI режиме (`autolycus`)

## 3. Проверка загрузки

### 3.1. Плагин загружен?

**Команда:**
```
/sbl status
```

**Ожидаемый результат (первый запуск, снапшота ещё нет):**
```
SBL: No snapshot. First SYSTEM write will auto-snapshot.
```

**Ожидаемый результат (после снапшота):**
```
SBL Status: 12 services (snapshot), 28 configs
  Deep audit: 8 active services
  Cert users: 2
  SSL domains: 5
  Changes applied: 0
```

Если команда `/sbl` не распознана — плагин не загружен. Проверь
`plugins.enabled` в конфиге.

### 3.2. Принудительный снапшот

```
/sbl snapshot
```

**Ожидаемый результат:**
```
SBL Snapshot updated:
  • 12 services
  • 28 config dependencies
  • 0 learned changes
```

## 4. Классификация путей

### 4.1. USER path — должно пропустить

USER-пути: `/home/`, `/tmp/`, `/root/`, `/var/tmp/`.

**Действие:** скажи агенту:
```
запиши "test content" в файл /tmp/sbl-test.txt
```

**Ожидаемый результат:** файл создаётся. SBL не вмешивается.

**Проверка:**
```
/sbl changes
```

**Ожидаемый результат:**
```
SBL: No changes recorded.
```

SBL не учится на USER-путях.

### 4.2. UNKNOWN path — должно заблокировать

**Действие:**
```
запиши "test" в файл /some-unknown-path/test.txt
```

**Ожидаемый результат:** в ответе агента появится блокировка:
```
[SBL] Unclassified path: '/some-unknown-path/test.txt' — blocked.
  Use known paths under /etc/, /opt/, /usr/, or user paths under /home/, /tmp/
```

### 4.3. SYSTEM path — снапшот + проверка зависимостей

**Действие:**
```
запиши "test nginx config" в файл /etc/nginx/sbl-test.txt
```

**Ожидаемый результат:** SBL:
1. Автоматически снимает снапшот (если ещё не был снят)
2. Находит что `/etc/nginx/` принадлежит сервису nginx
3. Показывает предупреждение:

```
[SBL] Writing to /etc/nginx/sbl-test.txt affects running services:
  • nginx [systemd] — port 443 — via /etc/nginx/
```

Если nginx не установлен, SBL покажет предупреждение, но не найдёт
зависимостей — и пропустит запись.

**Проверка после записи:**
```
/sbl changes
```

SBL запомнил новый файл:
```
SBL Change Log (1 entries):
  [2026-05-14T...] write_file → /etc/nginx/sbl-test.txt
```

## 5. Dependency Map

### 5.1. Полная карта зависимостей

```
/sbl deps
```

**Ожидаемый результат:** список всех известных SBL путей и сервисов:
```
SBL Full Dependency Map:
  /etc/nginx/ → nginx
  /etc/ssh/sshd_config → ssh
  /etc/fail2ban/ → fail2ban
  /etc/hosts → networking
  /etc/letsencrypt/ → certbot, nginx
  /opt/stalwart/ → stalwart
  /usr/local/etc/xray/ → xray
```

### 5.2. Проверка зависимости конкретного пути

```
/sbl deps /etc/nginx/nginx.conf
```

**Ожидаемый результат:**
```
Dependencies for /etc/nginx/nginx.conf:
  • nginx [systemd] — port 443 — via /etc/nginx/
```

### 5.3. Путь без зависимостей

```
/sbl deps /etc/nonexistent-service/
```

**Ожидаемый результат:**
```
No known dependencies for /etc/nonexistent-service/
```

## 6. Deep Audit

Проверка полного аудита системы (fd + rg + stdlib):

```
/sbl deep-audit
```

**Ожидаемый результат:** сводка по всем обнаруженным сервисам,
конфигам и сертификатам:
```
=== INFRASTRUCTURE AUDIT ===

SERVICES (9):
  nginx  [systemd]             ports: 80, 443
  ssh    [systemd]             ports: 22
  fail2ban [systemd]           configs: 1
  ...

CONFIGS (32):
  /etc/nginx/nginx.conf
  /etc/ssh/sshd_config
  ...

SSL CERT USERS (2):
  certbot (5 domains): autolycus-agent.ru, ...
  nginx (5 domains): same set
```

Если команда не найдена — установи fd и rg:
```bash
apt install fd-find ripgrep
```

## 7. RTK не трогает SBL-сообщения

Проверка что SBL-блокировки не обрезаются RTK-фильтром:

1. Включи RTK (по умолчанию включён)
2. Выполни тест из п.4.2 (UNKNOWN path)
3. Сообщение о блокировке должно прийти **полностью**, без head/tail обрезки

SBL возвращает dict `{"action": "block", "message": "..."}`, а RTK
фильтрует только string-результаты (через `transform_tool_result`).
Блокировки SBL проходят через `pre_tool_call` — другой хук, RTK их
не трогает.

## 8. Персистентность между сессиями

1. Выполни снапшот: `/sbl snapshot`
2. Выйди из агента: `/exit`
3. Запусти заново: `autolycus`
4. Проверь: `/sbl status`

Снапшот должен быть на месте (данные на диске), статус показывает
те же сервисы. Deep audit не запускается повторно.

## 9. Сброс

```
/sbl reset
```

Удаляет все снапшоты и обученные зависимости. Начать с чистого листа.

## 10. Сводная таблица проверок

| # | Проверка | Команда / Действие | Ожидаемый результат |
|---|----------|-------------------|---------------------|
| 1 | Плагин загружен | `/sbl status` | Статус или «No snapshot» |
| 2 | Принудительный снапшот | `/sbl snapshot` | N services, M configs |
| 3 | USER path | write_file в `/tmp/` | Файл создан |
| 4 | UNKNOWN path | write_file в `/unknown/` | Блокировка SBL |
| 5 | SYSTEM path | write_file в `/etc/nginx/` | Предупреждение о зависимостях |
| 6 | Dependency map | `/sbl deps` | Список путей → сервисы |
| 7 | Конкретная зависимость | `/sbl deps /etc/ssh/sshd_config` | ssh |
| 8 | Deep audit | `/sbl deep-audit` | Сводка сервисов/конфигов |
| 9 | Персистентность | `/exit` → `autolycus` → `/sbl status` | Те же данные |
| 10 | Сброс | `/sbl reset` | Пустой SBL |

## 11. Критерии прохождения

Все 10 проверок проходят без ошибок.

- **П.1-2:** подтверждают что плагин жив и активен
- **П.3:** USER paths не блокируются (SBL не мешает нормальной работе)
- **П.4:** UNKNOWN paths блокируются (SBL защищает от случайных записей)
- **П.5:** SYSTEM paths с зависимостями показывают предупреждение
- **П.6-7:** Dependency map корректна и соответствует реальной
  конфигурации системы
- **П.8:** Deep audit завершается без ошибок (опционально — нужны fd+rg)
- **П.9:** Данные переживают рестарт агента
- **П.10:** Сброс работает (clean slate)
