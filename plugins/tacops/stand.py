"""
plugins/tacops/stand.py — Модели данных для описания стенда

Стенд = bastion + N доменов ALD Pro + клиенты + сервисы,
разложенные по сегментированным сетям.

Bastion — центральный элемент:
  - маршрутизатор между сегментами (NIC на каждую сеть)
  - DNS (bind9) для внутренних доменов
  - NAT для выхода в интернет
  - Port forwarding для доступа к ВМ извне
  - ProxyJump для Ansible

Ansible работает через bastion: ProxyCommand → ssh -W %h:%p user@bastion
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── Провайдер виртуализации ──────────────────────────────────────────────────

class ProviderType(Enum):
    OPENNEBULA = "opennebula"
    PROXMOX = "proxmox"

    @classmethod
    def from_str(cls, s: str) -> "ProviderType":
        for p in cls:
            if p.value == s.lower():
                return p
        raise ValueError(f"Unknown provider: {s}")


# ── Сеть ─────────────────────────────────────────────────────────────────────

@dataclass
class NetworkSpec:
    """Сегмент сети VLAN."""
    name: str               # dvisarch-nonroutable-5
    cidr: str               # 10.220.115.0/24
    vlan: int               # 310
    gateway: str            # 10.220.115.1 — обычно IP бастиона в этой сети
    routable: bool = False  # есть выход в интернет?
    description: str = ""   # "VLAN 310: region1-msad, region1-ald"
    id: int = 0             # OpenNebula Network ID (заполняется при поиске)

    @property
    def netmask(self) -> str:
        """Вернуть маску из CIDR (24 → 255.255.255.0)."""
        from ipaddress import ip_network
        return str(ip_network(self.cidr, strict=False).netmask)


# ── Bastion ──────────────────────────────────────────────────────────────────

@dataclass
class PortForwardRule:
    """Правило проброса портов через bastion."""
    name: str               # keycloak-web, ssh-dc1
    external_port: int      # 8443, 2201
    internal_ip: str        # 100.64.0.69
    internal_port: int      # 8443, 22
    protocol: str = "tcp"


@dataclass
class BastionSpec:
    """
    Bastion host — центральный шлюз стенда.

    От количества сетей зависит количество NIC и маршрутов.
    Bastion всегда:
      - имеет NIC в management/public сети
      - NIC в каждой внутренней сети (gateway для неё)
      - DNS forwarder (bind9) для внутренних доменов
      - NAT masquerade для internal → internet
      - Port forwarding для external → internal сервисов
    """
    # Характеристики ВМ
    cpu: int = 2
    vcpu: int = 2
    ram_mb: int = 2048
    disk_gb: int = 20
    image_id: int = 1438            # "bip" образ по умолчанию

    # Какие сети обслуживает (NIC на каждую)
    # Первая в списке — management (выход в интернет)
    networks: list[str] = field(default_factory=list)

    # DNS домены, которые форвардит bind9
    dns_forward_zones: list[str] = field(default_factory=lambda: [
        "local", "novatek.int", "novatek.pri",
    ])

    # Проброс портов
    port_forwarding: list[PortForwardRule] = field(default_factory=list)

    # SSH ключ для доступа к ВМ через bastion
    ssh_key_name: str = "id_ed25519"


# ── Виртуальная машина ──────────────────────────────────────────────────────

@dataclass
class VmSpec:
    """Описание виртуальной машины в стенде."""
    name: str               # ald-pro-dc-1, client-dc1, keycloak-server
    cpu: int = 2
    vcpu: int = 2
    ram_mb: int = 4096
    disk_gb: int = 30
    image_id: int = 0       # будет заполнено позже или указано явно

    # К каким сетям подключена (имена из StandSpec.networks)
    networks: list[str] = field(default_factory=list)

    # ALD Pro домен, к которому принадлежит
    aldpro_domain: str = ""

    # Роли/сервисы (keycloak, mail, workspad, client...)
    roles: list[str] = field(default_factory=list)

    # Описание для тегов OpenNebula
    description: str = ""

    def ip_in_network(self, network_name: str, network_map: dict[str, NetworkSpec]) -> str:
        """
        Сгенерировать IP для ВМ в указанной сети.
        По умолчанию: network.gateway заменяем последний октет на 10 + индекс.
        """
        net = network_map.get(network_name)
        if not net:
            return ""
        # gateway: 10.220.115.1 → префикс 10.220.115.
        prefix = ".".join(net.gateway.rsplit(".", 1)[0]) + "."
        # Определяем номер по индексу машины в сети
        host_part = 10 + hash(self.name) % 240
        return f"{prefix}{host_part}"


# ── Домен ALD Pro ───────────────────────────────────────────────────────────

@dataclass
class DomainSpec:
    """Домен ALD Pro."""
    name: str               # res.local, ipa1.local
    realm: str = ""         # RES.LOCAL (авто: upper(name))
    admin_password: str = ""

    def __post_init__(self):
        if not self.realm:
            self.realm = self.name.upper()

    @property
    def short_name(self) -> str:
        """res.local → res"""
        return self.name.split(".")[0]


# ── Credentials ──────────────────────────────────────────────────────────────

@dataclass
class Credentials:
    """Учётные данные для развёртывания стенда."""
    # OpenNebula
    one_endpoint: str = ""          # задаётся через env ONE_ENDPOINT или в orchestrator
    one_username: str = ""           # задаётся через env ONE_USERNAME
    one_token: str = ""

    # ВМ
    vm_password: str = ""
    admin_username: str = "astra"

    # SSH
    ssh_public_key_path: str = "~/.ssh/id_ed25519.pub"
    ssh_private_key_path: str = "~/.ssh/id_ed25519"

    # ALD Pro
    aldpro_admin_password: str = ""

    # Ansible Galaxy
    galaxy_published_token: str = ""
    galaxy_validated_token: str = ""


# ── Ansible конфигурация ────────────────────────────────────────────────────

@dataclass
class AnsibleConfig:
    """
    Конфигурация Ansible для стенда.

    Все хосты доступны через bastion (ProxyCommand).
    """
    bastion_ip: str = ""       # заполняется после terraform apply
    ssh_user: str = "astra"
    ssh_key_path: str = "~/.ssh/id_ed25519"

    # Коллекции для установки
    collections: list[str] = field(default_factory=lambda: [
        "astra.ald_pro",
        "astra.astralinux",
        "astra.keycloak",
        "astra.docker",
        "ansible.utils",
        "community.general",
        "community.crypto",
        "community.docker",
        "community.postgresql",
    ])

    # Фазы развёртывания (файлы playbook)
    phases: list[str] = field(default_factory=lambda: [
        "01_bastion_setup.yml",
        "02_infrastructure.yml",
        "03_aldpro_deployment.yml",
        "04_keycloak_docker.yml",
        "05_external_users_setup.yml",
        "06_keycloak_test_app.yml",
        "10_smartcard_setup.yml",
        "12_smartcard_auth_test.yml",
    ])

    def proxy_command(self) -> str:
        """Ansible ProxyCommand для доступа к ВМ через bastion."""
        return (
            f"ssh -o StrictHostKeyChecking=no "
            f"-o UserKnownHostsFile=/dev/null "
            f"-W %h:%p -q {self.ssh_user}@{self.bastion_ip} "
            f"-i {self.ssh_key_path}"
        )


# ── Спецификация стенда (главная модель) ────────────────────────────────────

@dataclass
class StandSpec:
    """
    Полная спецификация стенда.

    Используется для генерации Terraform + Ansible.
    """
    # Мета
    name: str                       # novatek-stand-0109
    description: str = ""
    workdir: str = ""               # куда генерировать (пользователь указывает)

    # Провайдер
    provider: ProviderType = ProviderType.OPENNEBULA

    # Префикс для именования ресурсов в OpenNebula
    resources_prefix: str = ""

    # Сети
    networks: list[NetworkSpec] = field(default_factory=list)

    # Bastion — всегда один
    bastion: BastionSpec = field(default_factory=BastionSpec)

    # ВМ
    domains: list[DomainSpec] = field(default_factory=list)  # ALD Pro DC
    clients: list[VmSpec] = field(default_factory=list)       # клиенты доменов
    services: list[VmSpec] = field(default_factory=list)      # keycloak, mail...
    extra_vms: list[VmSpec] = field(default_factory=list)     # прочие

    # Credentials
    creds: Credentials = field(default_factory=Credentials)

    # Ansible
    ansible: AnsibleConfig = field(default_factory=AnsibleConfig)

    # Labels для OpenNebula
    labels: dict[str, str] = field(default_factory=lambda: {
        "project": "tacops",
        "owner": "tacops",
    })

    # Все ВМ одним списком (для итерации)
    @property
    def all_vms(self) -> list[VmSpec]:
        vms = []
        vms.extend(self.clients)
        vms.extend(self.services)
        vms.extend(self.extra_vms)
        return vms

    # Сети для bastion (все сети + management)
    @property
    def bastion_networks(self) -> list[str]:
        """Сети, к которым подключён bastion."""
        mgmt_name = "management"
        internal_names = [n.name for n in self.networks if not n.routable]
        return [mgmt_name] + list(dict.fromkeys(self.bastion.networks or internal_names))


# ── План развёртывания ──────────────────────────────────────────────────────

@dataclass
class DeployPlan:
    """Результат планирования — что и в каком порядке делать."""
    spec: StandSpec
    stand_dir: str                  # рабочий каталог стенда
    tf_dir: str                     # terraform/
    ansible_dir: str                # ansible/

    # Проверки перед развёртыванием
    checks: list[dict] = field(default_factory=list)  # [{check, status}]

    # Выходные данные Terraform
    state: dict = field(default_factory=dict)

    # Статус выполнения
    status: str = "planned"         # planned | deploying | deployed | failed | destroyed


@dataclass
class DeployResult:
    """Результат развёртывания."""
    status: str                     # deployed | failed | partial
    vms: dict[str, str] = field(default_factory=dict)     # имя → IP
    bastion_ip: str = ""
    ansible_phases: dict[str, str] = field(default_factory=dict)  # фаза → ok/failed
    error: str = ""
