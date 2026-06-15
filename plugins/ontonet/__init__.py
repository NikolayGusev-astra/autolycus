"""
plugins/ontonet — Onto MCP plugin for Autolycus

Provides MCP client for Onto knowledge graph:
- create_entity, search_entities, delete_entity
- create_relation, search_objects
- create_diagram, add_nodes_to_diagram
- save_template, get_template

Usage:
    from plugins.ontonet import client
    result = client.create_entity(realm_id="...", name="...", description="...")
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_API_KEY = "api-key-8a739b71-87bd-47d8-b9db-2d7695334f2f"
DEFAULT_MCP_URL = "https://app.ontonet.ru/mcp"
DEFAULT_REALM_ID = "4562b68a-98a0-4e68-a3a4-f36c5c10ff49"

# ---------------------------------------------------------------------------
# MCP Client
# ---------------------------------------------------------------------------

class OntoMCPClient:
    """Thread-safe MCP client for Onto knowledge graph."""

    def __init__(self, api_key: str = DEFAULT_API_KEY, mcp_url: str = DEFAULT_MCP_URL):
        self._api_key = api_key
        self._mcp_url = mcp_url
        self._session_id: Optional[str] = None
        self._req_id = 0
        self._lock = threading.Lock()
        self._tools_cache: Optional[List[str]] = None

    def _call(self, method: str, params: Any = None) -> Optional[Any]:
        """Make an MCP JSON-RPC call."""
        import requests

        with self._lock:
            self._req_id += 1
            req_id = self._req_id

        payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params:
            payload["params"] = params

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "X-Onto-Api-Key": self._api_key,
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        try:
            r = requests.post(self._mcp_url, json=payload, headers=headers, timeout=30)
            if "mcp-session-id" in r.headers and not self._session_id:
                self._session_id = r.headers["mcp-session-id"]

            for line in r.text.split("\n"):
                if line.startswith("data: {"):
                    data = json.loads(line[6:])
                    if "result" in data:
                        return data["result"]
                    if "error" in data:
                        logger.error("MCP error: %s", data["error"])
                        return None
        except Exception as e:
            logger.error("MCP call failed (%s): %s", method, e)
            return None
        return None

    def _tool(self, name: str, args: Dict[str, Any]) -> Optional[Any]:
        """Call an MCP tool."""
        result = self._call("tools/call", {"name": name, "arguments": args})
        if result and isinstance(result, dict) and "content" in result:
            texts = [c.get("text", "") for c in result["content"] if c.get("type") == "text"]
            if texts:
                combined = "\n".join(texts)
                # Try to extract UUID from response
                return combined
        return result

    def initialize(self) -> bool:
        """Initialize MCP session."""
        result = self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "autolycus-ontonet", "version": "1.0"}
        })
        time.sleep(0.5)
        return result is not None

    def list_tools(self) -> List[str]:
        """List available MCP tool names."""
        if self._tools_cache:
            return self._tools_cache
        result = self._call("tools/list")
        if result and isinstance(result, dict):
            tools = result.get("tools", [])
            self._tools_cache = [t["name"] for t in tools]
            return self._tools_cache
        return []

    @staticmethod
    def _uid(text: str) -> Optional[str]:
        """Extract UUID from text."""
        m = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', text or "")
        return m.group() if m else None

    # --- Template operations ---

    def save_template(self, realm_id: str, name: str, description: str = "",
                      color: str = "") -> Optional[str]:
        """Create or update a template. Returns template UUID."""
        args: Dict[str, Any] = {
            "realm_id": realm_id,
            "name": name,
        }
        if description:
            args["comment"] = description
        # Note: 'color' is not a valid MCP parameter for save_template
        result = self._tool("save_template", args)
        return self._uid(str(result)) if result else None

    def get_template(self, realm_id: str, template_id: str) -> Optional[str]:
        """Get template details."""
        return self._tool("get_template", {"realm_id": realm_id, "template_id": template_id})

    def search_templates(self, realm_id: str, name_part: str = "") -> Optional[str]:
        """Search templates by name."""
        args: Dict[str, Any] = {"realm_id": realm_id}
        if name_part:
            args["name_part"] = name_part
        return self._tool("search_templates", args)

    # --- Entity operations ---

    def save_entity(self, realm_id: str, name: str, description: str = "",
                    meta_entity_id: Optional[str] = None) -> Optional[str]:
        """Create or update an entity. Returns entity UUID."""
        args: Dict[str, Any] = {
            "realm_id": realm_id,
            "name": name,
        }
        if description:
            args["comment"] = description
        if meta_entity_id:
            args["meta_entity_id"] = meta_entity_id
        result = self._tool("save_entity", args)
        return self._uid(str(result)) if result else None

    def search_entities(self, realm_id: str, name_filter: str = "",
                       meta_entity_id: Optional[str] = None,
                       comment_filter: str = "",
                       include_inherited: bool = False,
                       offset: int = 0, limit: int = 50) -> Optional[str]:
        """Search entities by name filter and/or template (meta_entity_id).
        Returns raw text response with UUIDs."""
        args: Dict[str, Any] = {"realm_id": realm_id}
        if name_filter:
            args["name_filter"] = name_filter
        if meta_entity_id:
            args["meta_entity_id"] = meta_entity_id
        if comment_filter:
            args["comment_filter"] = comment_filter
        if include_inherited:
            args["include_inherited"] = include_inherited
        if offset:
            args["offset"] = offset
        if limit != 50:
            args["limit"] = limit
        return self._tool("search_entities", args)

    def search_objects(self, realm_id: str, name_filter: str = "",
                      template_uuid: Optional[str] = None,
                      comment_filter: str = "",
                      load_all: bool = False,
                      page_size: int = 50) -> Optional[str]:
        """Search objects (entities + diagrams) by name.
        Returns raw text response."""
        args: Dict[str, Any] = {"realm_id": realm_id}
        if name_filter:
            args["name_filter"] = name_filter
        if template_uuid:
            args["template_uuid"] = template_uuid
        if comment_filter:
            args["comment_filter"] = comment_filter
        if load_all:
            args["load_all"] = load_all
        if page_size != 50:
            args["page_size"] = page_size
        return self._tool("search_objects", args)

    def get_entity(self, realm_id: str, entity_id: str) -> Optional[str]:
        """Get entity details."""
        return self._tool("get_entity", {"realm_id": realm_id, "entity_id": entity_id})

    def delete_entity(self, realm_id: str, entity_id: str) -> bool:
        """Delete an entity (accepts single ID or list)."""
        result = self._tool("delete_entity", {
            "realm_id": realm_id,
            "entity_ids": [entity_id] if isinstance(entity_id, str) else entity_id,
        })
        return result is not None

    # --- Relation operations ---

    def create_relation(self, realm_id: str, source_id: str, target_id: str,
                       relation_type: str, start_role: str = "",
                       end_role: str = "") -> Optional[str]:
        """Create a relation between two entities."""
        args: Dict[str, Any] = {
            "realm_id": realm_id,
            "start_entity_id": source_id,
            "end_entity_id": target_id,
            "relation_type_name": relation_type,
        }
        if start_role:
            args["start_role"] = start_role
        if end_role:
            args["end_role"] = end_role
        result = self._tool("create_relation", args)
        return self._uid(str(result)) if result else None

    # --- Diagram operations ---

    def create_diagram(self, realm_id: str, name: str,
                      comment: str = "") -> Optional[str]:
        """Create a diagram. Returns diagram UUID."""
        args: Dict[str, Any] = {
            "realm_id": realm_id,
            "name": name,
        }
        if comment:
            args["comment"] = comment
        result = self._tool("create_diagram", args)
        return self._uid(str(result)) if result else None

    def add_nodes_to_diagram(self, realm_id: str, diagram_id: str,
                            node_ids: List[str]) -> bool:
        """Add existing entities to a diagram."""
        result = self._tool("add_existing_nodes_to_diagram", {
            "realm_id": realm_id,
            "diagram_id": diagram_id,
            "node_ids": node_ids,
        })
        return result is not None

    def search_diagrams(self, realm_id: str, name_part: str = "") -> Optional[str]:
        """Search diagrams by name."""
        args: Dict[str, Any] = {"realm_id": realm_id}
        if name_part:
            args["name_part"] = name_part
        return self._tool("search_diagrams", args)

    def get_diagram(self, realm_id: str, diagram_id: str) -> Optional[str]:
        """Get diagram details."""
        return self._tool("get_diagram", {"realm_id": realm_id, "diagram_id": diagram_id})


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------

_client: Optional[OntoMCPClient] = None
_client_lock = threading.Lock()


def get_client(api_key: str = DEFAULT_API_KEY,
               mcp_url: str = DEFAULT_MCP_URL,
               force_new: bool = False) -> OntoMCPClient:
    """Get or create the singleton OntoMCPClient."""
    global _client
    if _client is not None and not force_new:
        return _client
    with _client_lock:
        if _client is not None and not force_new:
            return _client
        _client = OntoMCPClient(api_key=api_key, mcp_url=mcp_url)
        _client.initialize()
        return _client


def reset_client() -> None:
    """Reset the singleton (for testing)."""
    global _client
    with _client_lock:
        _client = None


# ---------------------------------------------------------------------------
# Convenience functions (functional API)
# ---------------------------------------------------------------------------

def create_entity(realm_id: str, name: str, description: str = "",
                  meta_entity_id: Optional[str] = None) -> Optional[str]:
    """Create an entity. Returns UUID."""
    return get_client().save_entity(realm_id, name, description, meta_entity_id)


def search_entities(realm_id: str, name_filter: str = "",
                   meta_entity_id: Optional[str] = None) -> Optional[str]:
    """Search entities by name filter. Returns text with UUIDs."""
    return get_client().search_entities(realm_id, name_filter, meta_entity_id=meta_entity_id)


def create_relation(realm_id: str, source_id: str, target_id: str,
                   relation_type: str) -> Optional[str]:
    """Create a relation between two entities."""
    return get_client().create_relation(realm_id, source_id, target_id, relation_type)


def create_diagram(realm_id: str, name: str, comment: str = "") -> Optional[str]:
    """Create a diagram. Returns UUID."""
    return get_client().create_diagram(realm_id, name, comment)


def add_nodes_to_diagram(realm_id: str, diagram_id: str,
                        node_ids: List[str]) -> bool:
    """Add entities to a diagram."""
    return get_client().add_nodes_to_diagram(realm_id, diagram_id, node_ids)


def search_objects(realm_id: str, name_filter: str = "",
                  template_uuid: Optional[str] = None) -> Optional[str]:
    """Search objects by name filter. Returns text with UUIDs."""
    return get_client().search_objects(realm_id, name_filter, template_uuid=template_uuid)
