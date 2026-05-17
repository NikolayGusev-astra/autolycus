"""
plugins/tacops/orchestrator.py — Оркестратор развёртывания стенда

plan(spec) → генерирует terraform + ansible файлы в рабочий каталог
deploy(stand_dir) → terraform init → apply → ansible phases
destroy(stand_dir) → terraform destroy
status(stand_dir) → состояние стенда
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from plugins.tacops.stand import (
    Credentials, ProviderType, StandSpec,
)
from plugins.tacops import terraform as tf
from plugins.tacops import ansible as ans

logger = logging.getLogger(__name__)


def _ensure_dir(path: str) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def plan(spec: StandSpec, workdir: str, creds: Optional[Credentials] = None) -> dict:
    """
    Спланировать развёртывание стенда.

    Создаёт структуру:
      <workdir>/
      ├── terraform/
      │   ├── provider.tf, variables.tf, locals.tf
      │   ├── bastion.tf, main.tf, inventory.tf
      │   └── terraform.tfvars
      └── ansible/
          ├── ansible.cfg
          ├── requirements.yml
          └── inventory.json    (пустой, заполнится после terraform apply)

    Возвращает {status, stand_dir, tf_dir, ansible_dir, files}.
    """
    if creds:
        spec.creds = creds

    stand_dir = _ensure_dir(workdir)
    tf_dir = _ensure_dir(os.path.join(stand_dir, "terraform"))
    ansible_dir = _ensure_dir(os.path.join(stand_dir, "ansible"))

    # Генерируем terraform
    tf_files = tf.scaffold(spec)

    written = []
    for filename, content in tf_files.items():
        path = os.path.join(tf_dir, filename)
        with open(path, "w") as f:
            f.write(content)
        written.append(filename)
        logger.info("tacops: Wrote %s (%d chars)", path, len(content))

    # Генерируем ansible config и requirements
    ansible_cfg = ans.generate_ansible_cfg(
        inventory="../terraform/inventory.json",
        galaxy_published_token=spec.creds.galaxy_published_token,
        galaxy_validated_token=spec.creds.galaxy_validated_token,
    )
    with open(os.path.join(ansible_dir, "ansible.cfg"), "w") as f:
        f.write(ansible_cfg)
    written.append("ansible/ansible.cfg")

    req = ans.generate_requirements_yml(spec.ansible.collections)
    with open(os.path.join(ansible_dir, "requirements.yml"), "w") as f:
        f.write(req)
    written.append("ansible/requirements.yml")

    return {
        "status": "planned",
        "stand_dir": stand_dir,
        "tf_dir": tf_dir,
        "ansible_dir": ansible_dir,
        "files_written": written,
    }


def deploy(stand_dir: str,
           skip_tf_init: bool = False,
           skip_ansible: bool = False,
           phases: Optional[list[str]] = None,
           extra_vars: Optional[dict] = None) -> dict:
    """
    Развернуть стенд.

    1. terraform init
    2. terraform apply
    3. ansible-galaxy collection install
    4. ansible-playbook по фазам

    Возвращает {status, bastion_ip, vms, ansible_phases, error}.
    """
    tf_dir = os.path.join(stand_dir, "terraform")
    ansible_dir = os.path.join(stand_dir, "ansible")
    inventory = os.path.join(tf_dir, "inventory.json")

    if not os.path.isdir(tf_dir):
        return {"status": "error", "error": f"Not found: {tf_dir}"}

    result = {
        "status": "deploying",
        "bastion_ip": "",
        "vms": {},
        "ansible_phases": {},
        "error": "",
    }

    # Step 1: terraform init
    if not skip_tf_init:
        logger.info("tacops: terraform init...")
        ok, output = tf.init(tf_dir)
        if not ok:
            result["status"] = "failed"
            result["error"] = f"terraform init failed: {output[:1000]}"
            return result
        logger.info("tacops: terraform init OK")
    else:
        logger.info("tacops: Skipping terraform init")

    # Step 2: terraform apply
    logger.info("tacops: terraform apply...")
    ok, output = tf.apply(tf_dir)
    if not ok:
        result["status"] = "failed"
        result["error"] = f"terraform apply failed: {output[:1000]}"
        return result
    logger.info("tacops: terraform apply OK")

    # Получаем IP ВМ из terraform output
    tf_outputs = tf.output(tf_dir)
    result["bastion_ip"] = tf_outputs.get("bastion_ip", {}).get("value", "")
    vms = tf.get_vm_ips(tf_dir)
    result["vms"] = {name: ips[0] if ips else "" for name, ips in vms.items()}

    if skip_ansible:
        result["status"] = "deployed"
        result["ansible_phases"] = {"note": "skipped"}
        return result

    # Step 3: install collections
    logger.info("tacops: Installing ansible collections...")
    ok, output = ans.install_collections(ansible_dir)
    if not ok:
        logger.warning("tacops: Some collections failed: %s", output[:500])
        result["ansible_phases"]["collections"] = "warning"

    # Step 4: run ansible phases
    logger.info("tacops: Running ansible phases...")
    phase_results = ans.run_all_phases(
        inventory=inventory,
        workdir=ansible_dir,
        phases=phases,
        extra_vars=extra_vars,
    )

    result["ansible_phases"] = {
        phase: "ok" if info.get("ok") else "failed"
        for phase, info in phase_results.items()
    }

    all_ok = all(v == "ok" for v in result["ansible_phases"].values())
    result["status"] = "deployed" if all_ok else "partial"

    return result


def destroy(stand_dir: str, auto_approve: bool = True) -> dict:
    """Уничтожить стенд: terraform destroy."""
    tf_dir = os.path.join(stand_dir, "terraform")
    if not os.path.isdir(tf_dir):
        return {"status": "error", "error": f"Not found: {tf_dir}"}

    ok, output = tf.destroy(tf_dir)
    return {
        "status": "destroyed" if ok else "failed",
        "output_preview": output[:500],
        "error": "" if ok else output[:500],
    }


def status(stand_dir: str) -> dict:
    """Статус стенда по terraform state."""
    tf_dir = os.path.join(stand_dir, "terraform")
    state_path = os.path.join(tf_dir, "terraform.tfstate")
    inv_path = os.path.join(tf_dir, "inventory.json")

    if not os.path.isdir(tf_dir):
        return {"status": "not_found", "stand_dir": stand_dir}

    state_exists = os.path.isfile(state_path)
    inv_exists = os.path.isfile(inv_path)

    result = {
        "status": "unknown",
        "stand_dir": stand_dir,
        "terraform_state": "exists" if state_exists else "missing",
        "inventory": "exists" if inv_exists else "missing",
    }

    if state_exists:
        try:
            state = tf.state(tf_dir)
            resources = state.get("resources", [])
            vms = {}
            for r in resources:
                if r.get("type") != "opennebula_virtual_machine":
                    continue
                name = r.get("name", "unknown")
                instances = r.get("instances", [{}])
                attrs = instances[0].get("attributes", {}) if instances else {}
                nics = attrs.get("nic", [])
                ips = [n.get("ip", "") for n in nics if isinstance(n, dict) and n.get("ip")]
                status_str = instances[0].get("status", "?") if instances else "?"
                vms[name] = {
                    "ips": ips,
                    "status": status_str,
                }
            result["vms"] = vms
            result["status"] = "deployed" if vms else "empty"
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            result["error"] = str(e)

    return result
