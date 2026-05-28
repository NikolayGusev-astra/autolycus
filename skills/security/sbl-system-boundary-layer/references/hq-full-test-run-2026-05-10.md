# HQ Full Test Run — 2026-05-10

## Контекст

После мержа трёх веток (`feat/sbl-goal + feat/sanitize-mcp + feat/sanitize-pipeline`) в одну (`feat/sbl-goal`) и активации SBL плагина на HQ (NL VPS) — прогон всех тестов на реальном сервере.

## Результаты

| Модуль | Файл | Команда | Результат |
|--------|------|---------|-----------|
| Supply Chain | `core/supply_chain.py` | `pytest tests/test_supply_chain.py -o "addopts="` | ✅ 34/34 PASSED |
| MCP Validation | `plugins/sanitize_mcp/` | `pytest tests/test_sanitize_mcp.py -o "addopts="` | ✅ 18/18 PASSED |
| Sanitize Pipeline | `core/sanitize.py` | `pytest tests/test_sanitize.py -o "addopts="` | ✅ 26/26 PASSED |
| SBL | `plugins/sbl/__init__.py` | `python tests/test_sbl_prototype.py` | ✅ 9/9 PASSED |
| **Total** | | | **87 PASSED** |

## SBL — реальные данные с HQ

### FHS классификация (тест)
```
/etc/nginx/nginx.conf → SYSTEM
/home/user/test.txt   → USER
/tmp/test             → USER
/opt/hermes/test      → SYSTEM
/var/log/syslog       → SYSTEM
/unknown/path         → UNKNOWN
```

### Snapshot
- **Services:** 57
- **Ports:** 26
- **Config deps:** 19

### Прослушиваемые порты
```
:22     → sshd
:25     → stalwart
:53     → systemd-resolve
:80     → nginx
:443    → nginx
:465    → stalwart
:993    → stalwart
:995    → stalwart
:3000   → node
:3002   → node
:5432   → postgres
:8080   → stalwart
:8088   → python3
:8443   → nginx
:8551   → uvicorn
:8642   → python
:9091   → python3
:10022  → sshd
:11434  → ollama
:13000  → ssh
:18888  → ssh
:36721  → node
:41479  → stalwart
:4190   → stalwart
:4433   → xray
:4443   → xray
```

### Dependency lookup
```
/etc/nginx/nginx.conf → nginx (via /etc/nginx/)
/etc/ssh/sshd_config → sshd, ssh, ssh-tunnel-kozanout, ssh-tunnel-searxng, ssh-tunnel-grafana
/etc/stalwart/config.toml → no deps tracked (stalwart не матчится с _KNOWN_CONFIG_PATTERNS)
```

### Pre-write защита
- SYSTEM path (nginx.conf) → [SBL] предупреждение с зависимостями ✅
- USER path → passthrough ✅
- UNKNOWN path → blocked ✅
- systemctl restart → dependency lookup ✅
- echo > /etc/hosts → networking dependency ✅
- read_file → passthrough (не-write инструмент) ✅

## Баги, найденные при прогоне

1. **`port_owners` отсутствовал в `ServiceMap`** — dataclass имел только `services` и `file_owners`. Тест обращался к `sm.port_owners` → AttributeError. Фикс: добавлено поле + заполнение в `_take_snapshot()`.
2. **Хардкод пути в тесте** — `sys.path.insert(0, "/opt/hermes-victim-data")` не работает на HQ. Фикс: динамическое `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`.

## Команды для воспроизведения

```bash
cd /root/.hermes/hermes-agent
source .venv/bin/activate

# Supply Chain
python -m pytest tests/test_supply_chain.py -o "addopts=" -v 2>&1 | tail -5

# MCP Validation
python -m pytest tests/test_sanitize_mcp.py -o "addopts=" -v 2>&1 | tail -5

# Sanitize Pipeline
python -m pytest tests/test_sanitize.py -o "addopts=" -v 2>&1 | tail -5

# SBL prototype
python tests/test_sbl_prototype.py
```
