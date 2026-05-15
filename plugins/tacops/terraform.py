"""
plugins/tacops/terraform.py — Генерация Terraform по паттерну из реальных проектов

Генерирует .tf в стиле 8297 проекта:
  - locals.tf: маппинг образов, сетей, ВМ
  - bastion — отдельный resource с multi-NIC
  - Остальные ВМ — через for_each
  - inventory генерируется из state
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

from plugins.tacops.stand import (
    BastionSpec, Credentials, NetworkSpec,
    StandSpec, VmSpec,
)
from plugins.tacops import find_tool

logger = logging.getLogger(__name__)


def _tool(name: str) -> str:
    return find_tool(name)


def _hcl_val(v) -> str:
    """Значение → HCL строка."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_hcl_val(x) for x in v) + "]"
    if isinstance(v, dict):
        items = ", ".join(f'{k} = {_hcl_val(val)}' for k, val in v.items())
        return "{" + items + "}"
    return str(v)


def gen_provider(spec: StandSpec) -> str:
    return f"""terraform {{
  required_version = ">= 1.5.0"
  required_providers {{
    opennebula = {{
      source  = "OpenNebula/opennebula"
      version = "~> 1.4.0"
    }}
    local = {{
      source  = "hashicorp/local"
      version = "~> 2.5"
    }}
    tls = {{
      source  = "hashicorp/tls"
      version = "~> 4.1"
    }}
  }}
}}

provider "opennebula" {{
  endpoint = var.one_endpoint
  username = var.one_username
  password = var.one_token
  insecure = true
}}
"""


def gen_variables(spec: StandSpec) -> str:
    """variables.tf — минимальный набор."""
    return """variable "one_endpoint" { type = string sensitive = true }
variable "one_username" { type = string sensitive = true }
variable "one_token" { type = string sensitive = true }
variable "vm_password" { type = string sensitive = true }
variable "admin_username" { type = string }
variable "ssh_public_key_path" { type = string }
variable "ssh_private_key_path" { type = string }
variable "owner_name" { type = string }
variable "issue" { type = string }
variable "resources_prefix" { type = string }
variable "aldpro_admin_password" { type = string sensitive = true }
"""


def gen_locals(spec: StandSpec) -> str:
    """locals.tf — маппинг образов, сетей и ВМ."""
    lines = ['locals {', '  labels = {',
             '    owner   = var.owner_name',
             '    issue   = var.issue',
             '  }']

    # Image IDs
    lines.append('')
    lines.append('  # Image IDs')
    seen_ids = set()
    if spec.bastion.image_id:
        lines.append(f'  image_bastion = {spec.bastion.image_id}  # bastion')
        seen_ids.add(spec.bastion.image_id)
    for vm in spec.all_vms:
        iid = vm.image_id
        if iid and iid not in seen_ids:
            seen_ids.add(iid)
            safe = vm.name.replace("-", "_").replace(".", "_")
            lines.append(f'  image_{safe} = {iid}  # {vm.name}')

    # Network IDs
    lines.append('')
    lines.append('  # Network IDs')
    for net in spec.networks:
        if net.id:
            safe = net.name.replace("-", "_").replace(".", "_")
            lines.append(f'  net_{safe} = {net.id}  # {net.name}')

    # VM map
    lines.append('')
    lines.append('  # VM map')
    lines.append('  vm_map = {')
    for vm in spec.all_vms:
        if not vm.networks:
            continue
        safe = vm.name.replace("-", "_").replace(".", "_")
        net_key = vm.networks[0].replace("-", "_").replace(".", "_")
        img_ref = f"local.image_{safe}" if vm.image_id else "local.image_bastion"
        lines.append(f'    "{vm.name}" = {{')
        lines.append(f'      cpu           = {vm.cpu}')
        lines.append(f'      vcpu          = {vm.vcpu}')
        lines.append(f'      memory        = {vm.ram_mb}')
        lines.append(f'      size          = {vm.disk_gb * 1024}')
        lines.append(f'      image_id      = {img_ref}')
        lines.append(f'      network_key   = "{net_key}"')
        if vm.aldpro_domain:
            lines.append(f'      aldpro_domain = "{vm.aldpro_domain}"')
        if vm.roles:
            lines.append(f'      roles         = {_hcl_val(vm.roles)}')
        lines.append('    }')

    lines.append('  }')
    lines.append('')
    lines.append('  # Network IDs map')
    lines.append('  network_ids = {')
    for net in spec.networks:
        if net.id:
            safe = net.name.replace("-", "_").replace(".", "_")
            lines.append(f'    "{safe}" = local.net_{safe}')
    lines.append('  }')

    lines.append('}')
    return "\n".join(lines) + "\n"


def gen_bastion(spec: StandSpec) -> str:
    """main.tf — bastion resource."""
    b = spec.bastion
    nic_blocks = []
    for i, net_name in enumerate(spec.bastion_networks):
        safe = net_name.replace("-", "_").replace(".", "_")
        nic_blocks.append(f"""{{
      network_id = local.network_ids["{safe}"]
    }}""")

    nics = "\n".join(f"    {b}" for b in nic_blocks)

    return f"""resource "opennebula_virtual_machine" "bastion" {{
  name   = "${{var.resources_prefix}}-bastion"
  cpu    = {b.cpu}
  vcpu   = {b.vcpu}
  memory = {b.ram_mb}

  context = {{
    CONTEXT         = "true"
    NETWORK         = "YES"
    SET_HOSTNAME    = "${{var.resources_prefix}}-bastion"
    USERNAME        = var.admin_username
    PASSWORD_BASE64 = base64encode(var.vm_password)
    SSH_PUBLIC_KEY  = file(pathexpand(var.ssh_public_key_path))
    DNS             = "8.8.8.8"
  }}

  tags = {{
    AUTOSTARTVM   = "1"
    SERVICEUSERVM = "1"
    LABELS        = jsonencode(local.labels)
  }}

  disk {{
    size     = {b.disk_gb * 1024}
    image_id = local.image_bastion
  }}

{nics}

  graphics {{
    type   = "VNC"
    listen = "0.0.0.0"
  }}

  template_section {{
    name = "FEATURES"
    elements = {{ BALLOON = "1" }}
  }}

  template_section {{
    name = "CPU_MODEL"
    elements = {{ MODEL = "host-passthrough" }}
  }}

  timeouts {{
    create = "10m"
    update = "10m"
    delete = "5m"
  }}
}}

output "bastion_ip" {{
  value = opennebula_virtual_machine.bastion.nic[0].ip
}}
"""


def gen_nonbastion_vms(spec: StandSpec) -> str:
    """main.tf — остальные ВМ через for_each."""
    return """resource "opennebula_virtual_machine" "stand" {
  for_each = local.vm_map

  name   = "${var.resources_prefix}-${each.key}"
  cpu    = each.value.cpu
  vcpu   = each.value.vcpu
  memory = each.value.memory

  context = {
    CONTEXT         = "true"
    NETWORK         = "YES"
    SET_HOSTNAME    = "${var.resources_prefix}-${each.key}"
    USERNAME        = var.admin_username
    PASSWORD_BASE64 = base64encode(var.vm_password)
    SSH_PUBLIC_KEY  = file(pathexpand(var.ssh_public_key_path))
  }

  tags = {
    AUTOSTARTVM   = "1"
    SERVICEUSERVM = "1"
    LABELS        = jsonencode(local.labels)
  }

  disk {
    size     = each.value.size
    image_id = each.value.image_id
  }

  nic {
    network_id = local.network_ids[each.value.network_key]
  }

  graphics {
    type   = "VNC"
    listen = "0.0.0.0"
  }

  template_section {
    name = "FEATURES"
    elements = { BALLOON = "1" }
  }

  template_section {
    name = "CPU_MODEL"
    elements = { MODEL = "host-passthrough" }
  }

  timeouts {
    create = "10m"
    update = "10m"
    delete = "5m"
  }
}
"""


def gen_inventory(spec: StandSpec) -> str:
    """inventory.tf — Ansible inventory из state Terraform."""
    groups = {}

    if spec.domains:
        groups["pdcs"] = {}
        for d in spec.domains:
            groups["pdcs"][f"ald-pro-dc-{d.short_name}"] = {
                "aldpro_domain": d.name,
            }

    if spec.clients:
        groups["clients"] = {}
        for vm in spec.clients:
            groups["clients"][vm.name] = {
                "aldpro_domain": vm.aldpro_domain,
            }

    for vm in spec.services:
        for role in vm.roles:
            rk = f"{role}_servers"
            if rk not in groups:
                groups[rk] = {}
            groups[rk][vm.name] = {
                "aldpro_domain": vm.aldpro_domain,
            }

    # Gateway map: для bastion
    gateway_entries = []
    for i, net_name in enumerate(spec.bastion_networks):
        safe = net_name.replace("-", "_").replace(".", "_")
        gateway_entries.append(
            f'        "{safe}" = '
            f'join(".", slice(split(".", '
            f'opennebula_virtual_machine.bastion.nic[{i}].ip), 0, 3))'
        )
    gateway_body = "\n".join(gateway_entries)

    children_blocks = []
    for group_name, hosts in groups.items():
        if not hosts:
            continue
        host_entries = []
        for vm_name, vm_info in hosts.items():
            safe = vm_name.replace("-", "_").replace(".", "_")
            domain = vm_info.get("aldpro_domain", "")
            host_entries.append(f"""        {vm_name} = {{
          ansible_host  = opennebula_virtual_machine.stand["{vm_name}"].ip
          aldpro_domain = "{domain}"
        }}""")
        host_body = "\n".join(host_entries)
        children_blocks.append(f"""    {group_name} = {{
      hosts = {{
{host_body}
      }}
    }}""")
    children_body = "\n".join(children_blocks)

    return f"""resource "local_file" "ansible_inventory" {{
  filename       = "${{path.module}}/inventory.json"
  file_permission = "0644"

  content = jsonencode({{
    all = {{
      vars = {{
        ansible_user                 = var.admin_username
        ansible_python_interpreter   = "/usr/bin/python3"
        ansible_ssh_private_key_file = pathexpand(var.ssh_private_key_path)
        ansible_ssh_common_args      = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -W %h:%p -q ${{var.admin_username}}@${{opennebula_virtual_machine.bastion.nic[0].ip}} -i ${{pathexpand(var.ssh_private_key_path)}}"
        resources_prefix             = var.resources_prefix
        gateway_map = {{
{gateway_body}
        }}
      }}
      children = {{
{children_body}
      }}
    }}
  }})
}}

output "inventory_file" {{
  value = local_file.ansible_inventory.filename
}}
"""


def gen_tfvars(spec: StandSpec) -> str:
    """terraform.tfvars."""
    c = spec.creds
    lines = [
        f'one_endpoint          = "{c.one_endpoint}"',
        f'one_username          = "{c.one_username}"',
        f'one_token             = "{c.one_token}"',
        f'vm_password           = "{c.vm_password or "astra"}"',
        f'admin_username        = "{c.admin_username}"',
        f'ssh_public_key_path   = "{c.ssh_public_key_path}"',
        f'ssh_private_key_path  = "{c.ssh_private_key_path}"',
        f'owner_name            = "{spec.labels.get("owner", "tacops")}"',
        f'issue                 = "{spec.name}"',
        f'resources_prefix      = "{spec.resources_prefix or spec.name}"',
        f'aldpro_admin_password = "{c.aldpro_admin_password or "AstraLinux175"}"',
    ]
    return "\n".join(lines) + "\n"


def scaffold(spec: StandSpec) -> dict[str, str]:
    """Сгенерировать все файлы terraform для стенда."""
    return {
        "provider.tf": gen_provider(spec),
        "variables.tf": gen_variables(spec),
        "locals.tf": gen_locals(spec),
        "bastion.tf": gen_bastion(spec),
        "main.tf": gen_nonbastion_vms(spec),
        "inventory.tf": gen_inventory(spec),
        "terraform.tfvars": gen_tfvars(spec),
    }


# ── Запуск ──────────────────────────────────────────────────────────────────

def _run_tf(tf_dir: str, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    tf_bin = _tool("terraform")
    cmd = [tf_bin] + list(args)
    logger.info("tacops: Running: %s", " ".join(cmd))
    return subprocess.run(cmd, cwd=tf_dir, capture_output=True, text=True, timeout=timeout)


def init(tf_dir: str) -> tuple[bool, str]:
    r = _run_tf(tf_dir, "init", "-input=false")
    return r.returncode == 0, r.stdout + r.stderr


def apply(tf_dir: str) -> tuple[bool, str]:
    r = _run_tf(tf_dir, "apply", "-auto-approve", "-input=false", timeout=600)
    return r.returncode == 0, r.stdout + r.stderr


def destroy(tf_dir: str) -> tuple[bool, str]:
    r = _run_tf(tf_dir, "destroy", "-auto-approve", "-input=false", timeout=600)
    return r.returncode == 0, r.stdout + r.stderr
