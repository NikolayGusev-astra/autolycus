# portable — DevOps Swiss Army Knife

Набор инструментов для инженера без root-доступа.

## Состав

```
portable/
├── bin/             # статические бинарники (без зависимостей от системы)
│   ├── terraform    # v1.15.3 — IaC (OpenNebula, Proxmox)
│   ├── fd           # v10.4.2 — быстрый поиск файлов
│   ├── rg           # v15.1.0 — быстрый grep
│   ├── gh           # v2.92.0 — GitHub CLI
│   ├── jq           # v1.8.1 — JSON процессор
│   └── yq           # v4.53.2 — YAML процессор
├── venv/            # Python venv (создаётся install.sh)
│   ├── bin/ansible  # Ansible core 2.15+
│   ├── bin/ansible-playbook
│   └── lib/...      # pyone (OpenNebula API)
├── install.sh       # установка
├── versions.yaml    # версии + откуда качать
└── README.md
```

Все бинарники — статически скомпилированные, работают на любом Linux (x86_64) без единой зависимости.

## Установка

```bash
# Из корня autolycus:
bash portable/install.sh

# Через прокси (если геоблок):
ALL_PROXY=socks5://127.0.0.1:2081 bash portable/install.sh

# Только бинарники или только venv:
bash portable/install.sh --bin-only
bash portable/install.sh --venv-only
```

## Использование

```bash
# Добавить в PATH (на время сессии):
export PATH="$PWD/portable/bin:$PATH"
source portable/venv/bin/activate

# Или вызывать напрямую:
./portable/bin/terraform version
./portable/venv/bin/ansible-playbook site.yml
```

## Плагин tacops

Плагин tacops (`plugins/tacops/`) автоматически находит инструменты в portable/
и предоставляет их Hermes:

- `tacops.toolchain_report()` — сводка по всем инструментам
- `tacops.find_tool("terraform")` — путь к бинарнику
- `tacops.one_client()` — OpenNebula API клиент
- `tacops.terraform_version()` — версия

## Для добавления инструмента

1. Скачайте бинарник в `portable/bin/`
2. Добавьте версию + URL в `versions.yaml`
3. Добавьте find_tool в `__init__.py` при необходимости
4. Обновите install.sh
