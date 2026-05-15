"""
plugins/tacops/opennebula.py — OpenNebula API через XML-RPC

Поиск образов и сетей по описанию, проверка квот, статус ВМ.
"""
from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class OneImage:
    """Образ ВМ в OpenNebula."""
    id: int
    name: str
    size_mb: int
    state: str
    owner: str
    path: str = ""
    datastore: str = ""
    running_vms: int = 0
    has_gui: bool = False
    version: str = ""
    edition: str = ""

    @property
    def summary(self) -> str:
        return f"[{self.id}] {self.name} ({self.size_mb}MB, {self.state})"


@dataclass
class OneNetwork:
    """Сеть в OpenNebula."""
    id: int
    name: str
    bridge: str
    vlan: int
    owner: str
    type: str = ""
    group: str = ""


_STATES = {
    "0": "INIT", "1": "PENDING", "2": "READY", "3": "USED",
    "4": "DISABLED", "5": "LOCKED", "6": "ERROR", "7": "CLONE", "8": "DELETE",
}


class OneClient:
    """Клиент OpenNebula через XML-RPC."""

    def __init__(self, endpoint: str = "", username: str = "", token: str = ""):
        import ssl as ssl_mod
        import xmlrpc.client

        self.endpoint = endpoint or os.environ.get(
            "ONE_ENDPOINT", ""
        )
        if not self.endpoint:
            raise ValueError("ONE_ENDPOINT не задан. Укажите endpoint или установите переменную ONE_ENDPOINT")
        self.username = username or os.environ.get("ONE_USERNAME", "")
        if not self.username:
            raise ValueError("ONE_USERNAME не задан. Укажите username или установите переменную ONE_USERNAME")
        self.token = token or os.environ.get("ONE_TOKEN", "")

        if not self.token:
            raise ValueError(
                "ONE_TOKEN не задан. Укажите token или установите переменную ONE_TOKEN"
            )

        ssl_ctx = ssl_mod.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl_mod.CERT_NONE

        transport = xmlrpc.client.SafeTransport(context=ssl_ctx)
        self._api = xmlrpc.client.ServerProxy(
            self.endpoint, transport=transport, allow_none=True
        )
        self._auth = f"{self.username}:{self.token}"
        logger.info("OneClient: %s @ %s (xmlrpc)", self.username, self.endpoint)

    def _call(self, method: str, *args):
        """Вызов OpenNebula XML-RPC метода.
        method = 'one.imagepool.info' → server.one.imagepool.info(auth, ...)
        """
        parts = method.split(".")
        func = self._api
        for part in parts:
            func = getattr(func, part)
        result = func(self._auth, *args)
        if not result[0]:
            raise RuntimeError(f"OpenNebula API error ({method}): {result}")
        return result[1]

    # ── Образы ────────────────────────────────────────────────────────────────

    def list_images(self, owner: str = "") -> list[OneImage]:
        """Список образов. Фильтр по владельцу — owner (uname)."""
        xml_str = self._call("one.imagepool.info", -2, -1, -1)
        images = []
        for img in ET.fromstring(xml_str).findall("IMAGE"):
            name = img.findtext("NAME", "")
            uname = img.findtext("UNAME", "")
            if owner and uname != owner:
                continue
            labels_text = ""
            tmpl = img.find("TEMPLATE")
            if tmpl is not None:
                lbl = tmpl.find("LABELS")
                if lbl is not None and lbl.text:
                    labels_text = lbl.text

            version = ""
            ver_m = re.search(r"(?:SE\s+)?(\d+\.\d+(?:\.\d+)?(?:\.\w+)?)", name)
            if ver_m:
                version = ver_m.group(1)

            edition = ""
            for ed in ("Maximum", "Base", "GUI Base", "GUI"):
                if ed.lower() in name.lower():
                    edition = ed
                    break

            images.append(OneImage(
                id=int(img.findtext("ID", "0")),
                name=name,
                size_mb=int(img.findtext("SIZE", "0")),
                state=_STATES.get(img.findtext("STATE", ""), "?"),
                owner=uname,
                path=img.findtext("PATH", "") or "",
                datastore=img.findtext("DATASTORE", ""),
                running_vms=int(img.findtext("RUNNING_VMS", "0")),
                has_gui="С GUI" in labels_text or "gui" in name.lower(),
                version=version,
                edition=edition,
            ))
        return images

    def find_image(self, pattern: str, owner: str = "") -> Optional[OneImage]:
        """Найти образ по описанию. Имя, ID, версия, edition."""
        if pattern.isdigit():
            for img in self.list_images(owner=owner):
                if img.id == int(pattern):
                    return img
            return None

        images = self.list_images(owner=owner)
        kw = pattern.lower().split()
        scored = [(sum(1 for k in kw if k in f"{img.name} {img.version} {img.edition}".lower()), img)
                  for img in images if any(k in f"{img.name} {img.version} {img.edition}".lower() for k in kw)]
        if not scored:
            return None
        scored.sort(key=lambda x: (-x[0], -x[1].id))
        return scored[0][1]

    def suggest_images(self, query: str, limit: int = 5) -> list[OneImage]:
        """Предложить образы по запросу."""
        images = self.list_images()
        kw = query.lower().split()
        scored = [(sum(1 for k in kw if k in f"{img.name} {img.version} {img.edition}".lower()), img)
                  for img in images if any(k in f"{img.name} {img.version} {img.edition}".lower() for k in kw)]
        scored.sort(key=lambda x: (-x[0], -x[1].id))
        return [img for _, img in scored[:limit]]

    # ── Сети ──────────────────────────────────────────────────────────────────

    def list_networks(self) -> list[OneNetwork]:
        """Список сетей."""
        xml_str = self._call("one.vnpool.info", -2, -1, -1)
        networks = []
        for net in ET.fromstring(xml_str).findall("VNET"):
            name = net.findtext("NAME", "")
            net_type = "routable" if ("ext" in name or "rbta" in name) else "nonroutable"
            networks.append(OneNetwork(
                id=int(net.findtext("ID", "0")),
                name=name,
                bridge=net.findtext("BRIDGE", ""),
                vlan=int(net.findtext("VLAN_ID", "0") or "0"),
                owner=net.findtext("UNAME", ""),
                type=net_type,
                group=f"GID={net.findtext('GID', '?')}",
            ))
        return networks

    def find_network(self, pattern: str) -> Optional[OneNetwork]:
        """Найти сеть по описанию: имя, ID, vlan."""
        if pattern.isdigit():
            for net in self.list_networks():
                if net.id == int(pattern):
                    return net
        networks = self.list_networks()
        kw = pattern.lower().split()
        scored = [(sum(1 for k in kw if k in f"{net.name} vlan{net.vlan} {net.type}".lower()), net)
                  for net in networks if any(k in f"{net.name} vlan{net.vlan} {net.type}".lower() for k in kw)]
        if not scored:
            return None
        scored.sort(key=lambda x: (-x[0], -x[1].id))
        return scored[0][1]

    def suggest_networks(self, query: str, limit: int = 10) -> list[OneNetwork]:
        """Предложить сети по запросу."""
        networks = self.list_networks()
        kw = query.lower().split()
        scored = [(sum(1 for k in kw if k in f"{net.name} vlan{net.vlan} {net.type}".lower()), net)
                  for net in networks if any(k in f"{net.name} vlan{net.vlan} {net.type}".lower() for k in kw)]
        scored.sort(key=lambda x: (-x[0], -x[1].id))
        return [net for _, net in scored[:limit]]

    # ── Квоты ─────────────────────────────────────────────────────────────────

    def get_quota(self) -> dict:
        """Проверить квоту пользователя."""
        xml_str = self._call("one.user.quota.info")
        quotas = {}
        root = ET.fromstring(xml_str)
        for ds in root.findall(".//DATASTORE"):
            quotas[f"ds_{ds.get('id', '?')}"] = {
                "used": int(ds.findtext("size_used", "0")),
                "limit": int(ds.findtext("size_limit", "0")),
            }
        for vm_q in root.findall(".//VM"):
            quotas["vm"] = {
                "used": int(vm_q.findtext("vms_used", "0")),
                "limit": int(vm_q.findtext("vms_limit", "0")),
            }
        return quotas

    # ── Статус ВМ ─────────────────────────────────────────────────────────────

    def vm_status(self, vm_id: int) -> dict:
        """Статус ВМ по ID."""
        xml_str = self._call("one.vm.info", vm_id)
        root = ET.fromstring(xml_str)
        ips = [nic.findtext("IP", "") for nic in root.findall(".//NIC") if nic.findtext("IP", "")]
        return {
            "id": vm_id,
            "name": root.findtext("NAME", ""),
            "state": _STATES.get(root.findtext("STATE", ""), "?"),
            "ips": ips,
        }
