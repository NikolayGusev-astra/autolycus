# Tacops — Terraform + Ansible CoPilot System

Плагин для `autolycus` (hermes-agent), который позволяет Hermes-агенту автоматически развёртывать тестовые стенды на базе OpenNebula (или Proxmox) с последующей настройкой через Ansible.

## Философия

Инженер получает задачу — агент делает стенд.

```
Задача: "разверни стенд для задачи ПМИ-42"
  │
  ▼
Читает ТЗ → выбирает образы, сети → генерирует terraform → apply → ansible → стенд готов
```

Весь инструментарий идёт в комплекте (`portable/`), ничего не нужно устанавливать вручную.

## Структура

```
plugins/tacops/
├── __init__.py       # find_tool(), toolchain_report(), хуки
├── plugin.yaml       # метаданные плагина
├── stand.py          # модели: StandSpec, VmSpec, BastionSpec, NetworkSpec...
├── opennebula.py     # клиент OpenNebula (поиск образов/сетей по описанию)
├── terraform.py      # генерация .tf в стиле реальных проектов 8297
├── ansible.py        # обёртка ansible (facilities, collection install, фазы)
└── orchestrator.py   # оркестратор: plan → deploy → destroy → status
```

## Быстрый старт

### 1. Установить инструментарий

```bash
# Из корня autolycus:
bash portable/install.sh

# Если геоблок:
ALL_PROXY=socks5://127.0.0.1:2081 bash portable/install.sh
```

### 2. Настроить переменные окружения

```bash
# OpenNebula (корпоративный):
export ONE_ENDPOINT="https://ваш-сервер:2633/RPC2"
export ONE_USERNAME="ваш_логин"
export ONE_TOKEN="ваш_токен"

# Ansible Galaxy (Astra Automation Hub):
export ANSIBLE_GALAXY_SERVER_PUBLISHED_TOKEN="ваш_токен"
export ANSIBLE_GALAXY_SERVER_VALIDATED_TOKEN="ваш_токен"
```

### 3. Использование из Hermes

Агент (человек или другой агент) может вызывать tacops через Python API:

```python
from plugins.tacops.orchestrator import plan, deploy, status
from plugins.tacops.stand import *
from plugins.tacops.opennebula import OneClient

# 1. Получить образы и сети от провайдера
one = OneClient()
bip = one.find_image("bip")
astra = one.find_image("astra 1.7.8 base")
mgmt = one.find_network("dvisarch-nonroutable-0")
vlan310 = one.find_network("dvisarch-nonroutable-1")

# 2. Описать стенд
spec = StandSpec(
    name="my-stand",
    resources_prefix="my-stand",
    networks=[
        NetworkSpec("mgmt", "10.220.100.0/24", vlan=100,
                     gateway="10.220.100.1", routable=True,
                     name=mgmt.name, id=mgmt.id),
        NetworkSpec("vlan310", "10.220.115.0/24", vlan=310,
                     gateway="10.220.115.1",
                     name=vlan310.name, id=vlan310.id),
    ],
    bastion=BastionSpec(image_id=bip.id),
    domains=[DomainSpec("ipa1.local")],
    clients=[VmSpec("client-dc1", networks=["vlan310"],
                     image_id=astra.id)],
)

# 3. Сгенерировать terraform + ansible файлы
result = plan(spec, "/path/to/stand")

# 4. Развернуть
result = deploy(result["stand_dir"])
```

### 4. Slash-команды (скоро)

```
/stand plan /path/to/spec    — спланировать стенд
/stand up /path/to/stand     — развернуть
/stand down /path/to/stand   — уничтожить
/stand status /path/to/stand — статус
```

## Модели данных

### StandSpec
| Поле | Описание |
|------|----------|
| `name` | Имя стенда |
| `provider` | Провайдер (opennebula / proxmox) |
| `networks` | Список сегментов сети |
| `bastion` | Bastion-хост (всегда multi-NIC) |
| `domains` | Домены ALD Pro |
| `clients` | Клиентские машины |
| `services` | Сервисы (keycloak, mail...) |

### BastionSpec
Особенность: bastion всегда создаётся с NIC на **каждую сеть** стенда.
Он является:
- Маршрутизатором между сегментами
- DNS forwarder (bind9)
- NAT для выхода в интернет
- ProxyJump для Ansible

### Ansible через bastion
Ansible подключается к ВМ через bastion с ProxyCommand:
```ini
ansible_ssh_common_args = -o ProxyCommand="ssh -W %h:%p user@bastion"
```
Inventory генерируется **автоматически** из terraform state.

## Коллекции Ansible

Поддерживаются коллекции ГК Астра с Astra Automation Hub:
- `astra.ald_pro` — развёртывание ALD Pro доменов
- `astra.astralinux` — базовая настройка Astra Linux
- `astra.keycloak` — Keycloak SSO
- `astra.docker` — Docker

## Фазы развёртывания

Стенд разворачивается последовательно:

1. **bastion** — настройка bastion (DNS, NAT, port forwarding)
2. **infrastructure** — DNS, репозитории, пакеты
3. **aldpro** — развёртывание ALD Pro доменов
4. **keycloak** — Keycloak в Docker
5. **external_users** — внешние пользователи, Kerberos
6. **keycloak_apps** — тестовые приложения, SPNEGO
7. **smartcards** — SoftHSM2 эмуляция смарт-карт

Если фаза упала — развёртывание останавливается.

## Расширение

### Добавить провайдера
1. Создать модуль `plugins/tacops/провайдер.py`
2. Реализовать поиск образов/сетей (API клиент)
3. Реализовать генерацию provider.tf в `terraform.py`

### Добавить фазу Ansible
1. Написать playbook `<номер_>название.yml`
2. Добавить в `PHASE_PLAYBOOKS` в `ansible.py`
3. Указать фазу в `StandSpec.ansible.phases`

## Портфолио: portable/

Весь инструментарий в комплекте:
```
portable/
├── bin/
│   ├── terraform    # IaC
│   ├── fd, rg       # быстрый поиск
│   ├── gh           # GitHub CLI
│   ├── jq, yq       # JSON/YAML процессоры
├── venv/
│   ├── ansible-core # управление конфигурацией
│   └── pyone        # OpenNebula API
└── install.sh       # установка одной командой
```
