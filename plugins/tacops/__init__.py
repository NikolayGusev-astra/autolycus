"""
plugins/tacops — Terraform + Ansible CoPilot System

Обёртка над devops-инструментами из portable/autolycus.

Архитектура:
  __init__.py     → инициализация, поиск portable/, хуки
  toolchain.py    → поиск и вызов инструментов (terraform, ansible, jq, yq...)
  opennebula.py   → OpenNebula API через pyone
  ansible.py      → обёртка над ansible-playbook / ansible-galaxy

Плагин НЕ требует установки — находит portable/ относительно своего
расположения в autolycus:
    autolycus/
    ├── plugins/tacops/       ← здесь __init__.py
    └── portable/
        ├── bin/              ← статические бинарники
        └── venv/             ← Python с ansible + pyone
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Discovery: где лежит portable/ ───────────────────────────────────────────
# Порядок поиска:
#   1. Относительно plugins/tacops/ → ../../portable/
#   2. AUTOLYCUS_HOME/portable/
#   3. ~/.autolycus/portable/

def _discover_portable() -> Optional[Path]:
    """Найти корень portable/."""
    candidates = []

    # 1. Относительно этого файла
    this_file = Path(__file__).resolve()
    for p in [this_file.parent.parent.parent / "portable",
              Path.cwd() / "portable"]:
        candidates.append(p)

    # 2. AUTOLYCUS_HOME
    ah = os.environ.get("AUTOLYCUS_HOME", "")
    if ah:
        candidates.append(Path(ah) / "portable")

    # 3. ~/.autolycus/portable
    candidates.append(Path.home() / ".autolycus" / "portable")

    for p in candidates:
        p = p.resolve()
        if p.is_dir():
            logger.debug("tacops: portable найден в %s", p)
            return p

    logger.warning("tacops: portable/ не найден. "
                    "Плагин будет использовать system PATH.")
    return None


_PORTABLE: Optional[Path] = _discover_portable()


# ── Поиск инструментов ───────────────────────────────────────────────────────

def find_tool(name: str) -> str:
    """
    Вернуть полный путь к инструменту.

    Приоритет:
      1. portable/bin/<name>     (статический бинарник)
      2. portable/venv/bin/<name> (ansible, ansible-playbook)
      3. system PATH             (fallback)
    """
    if _PORTABLE:
        for subdir in ("bin", "venv/bin"):
            p = _PORTABLE / subdir / name
            if p.is_file():
                logger.debug("tacops: %s = %s", name, p)
                return str(p)

    # fallback — system PATH
    sys_path = shutil.which(name)
    if sys_path:
        logger.debug("tacops: %s = %s (system)", name, sys_path)
        return sys_path

    logger.warning("tacops: %s не найден ни в portable/, ни в PATH", name)
    return name  # вернём как есть — пусть упадёт с понятной ошибкой


def tool_version(name: str) -> str:
    """Версия инструмента (выполняет <tool> --version)."""
    path = find_tool(name)
    try:
        r = subprocess.run(
            [path, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip().split("\n")[0] or r.stderr.strip().split("\n")[0]
    except Exception as e:
        return f"error: {e}"


# ── Terraform helpers ────────────────────────────────────────────────────────

def terraform_path() -> str:
    return find_tool("terraform")


def terraform_version() -> str:
    return tool_version("terraform")


# ── Ansible helpers ──────────────────────────────────────────────────────────

def ansible_playbook_path() -> str:
    return find_tool("ansible-playbook")


def ansible_version() -> str:
    return tool_version("ansible")


def ansible_cfg_path() -> Optional[str]:
    """Вернуть путь к ansible.cfg из portable/venv/, если есть."""
    if _PORTABLE:
        cfg = _PORTABLE / "venv" / "ansible.cfg"
        if cfg.is_file():
            return str(cfg)
    return None


# ── OpenNebula helpers ───────────────────────────────────────────────────────

def pyone_available() -> bool:
    """Проверить, доступен ли pyone."""
    try:
        import pyone  # noqa: F401
        return True
    except ImportError:
        return False


def one_client(endpoint: str = "", username: str = "", token: str = ""):
    """Создать клиент OpenNebula (pyone.OneServer)."""
    from pyone import OneServer

    ep = endpoint or os.environ.get("ONE_ENDPOINT", "https://laika.astracloud.ru:2633/RPC2")
    usr = username or os.environ.get("ONE_USERNAME", "ngusev")
    tok = token or os.environ.get("ONE_TOKEN", "")

    return OneServer(ep, session=f"{usr}:{tok}")


# ── jq / yq helpers ─────────────────────────────────────────────────────────

def jq_path() -> str:
    return find_tool("jq")


def yq_path() -> str:
    return find_tool("yq")


# ── Сводка по инструментам ──────────────────────────────────────────────────

def toolchain_report() -> dict:
    """Вернуть словарь: инструмент → {путь, версия, статус}."""
    report = {}
    for name in ("terraform", "ansible", "ansible-playbook",
                  "fd", "rg", "gh", "jq", "yq"):
        path = find_tool(name)
        if path and Path(path).is_file() and path != name:
            ver = tool_version(name)
            report[name] = {"path": path, "version": ver, "status": "ok"}
        else:
            report[name] = {"path": "", "version": "", "status": "missing"}
    report["pyone"] = {
        "status": "available" if pyone_available() else "missing",
    }
    report["portable_dir"] = str(_PORTABLE) if _PORTABLE else "not found"
    return report


# ── Hook: on_session_start ──────────────────────────────────────────────────

def on_session_start() -> None:
    """При старте сессии — логируем найденный portable и базовые версии."""
    if _PORTABLE:
        logger.info("tacops: portable найден: %s", _PORTABLE)
        tools = toolchain_report()
        found = [n for n, v in tools.items() if v.get("status") == "ok"]
        missing = [n for n, v in tools.items() if v.get("status") == "missing"]
        logger.info("tacops: найдено %d инструментов: %s", len(found), ", ".join(found))
        if missing:
            logger.warning("tacops: отсутствуют: %s", ", ".join(missing))
    else:
        logger.warning("tacops: portable/ не найден. "
                        "Запустите bash portable/install.sh")
