#!/usr/bin/env python3
"""
ontono — CLI утилита для работы с Onto knowledge graph

Usage:
    onto search "merge"                    # поиск сущностей
    onto create --name "test" --desc "..."  # создать сущность
    onto list                               # список диаграмм
    onto tools                              # список MCP инструментов

Зависит от плагина plugins/ontonet.
"""

import argparse
import json
import logging
import os
import sys

# Add repo root to path.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from plugins.ontonet import get_client, DEFAULT_REALM_ID
from plugins.ontonet.diff_to_onto import apply_plan, build_plan, render_plan, repo_root_from, to_dict

logger = logging.getLogger(__name__)


def cmd_search(args):
    """Search entities in Onto."""
    client = get_client()
    results = client.search_entities(
        realm_id=args.realm or DEFAULT_REALM_ID,
        name_filter=args.query,
        template_uuid=args.template,
    )
    if results:
        if isinstance(results, list):
            for r in results:
                if isinstance(r, dict):
                    name = r.get("name", "?")
                    eid = r.get("id", r.get("uuid", "?"))
                    desc = r.get("description", "")[:80]
                    print(f"  {name} [{eid}]")
                    if desc:
                        print(f"    {desc}")
                else:
                    print(f"  {r}")
        else:
            print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print("  No results found.")


def cmd_create(args):
    """Create an entity in Onto."""
    client = get_client()
    eid = client.save_entity(
        realm_id=args.realm or DEFAULT_REALM_ID,
        name=args.name,
        description=args.desc or "",
        meta_entity_id=args.template,
    )
    if eid:
        print(f"  ✅ Created entity: {eid}")
    else:
        print("  ❌ Failed to create entity.")


def cmd_relation(args):
    """Create a relation between entities."""
    client = get_client()
    rid = client.create_relation(
        realm_id=args.realm or DEFAULT_REALM_ID,
        source_id=args.source,
        target_id=args.target,
        relation_type=args.type,
    )
    if rid:
        print(f"  ✅ Created relation: {rid} ({args.type})")
    else:
        print("  ❌ Failed to create relation.")


def cmd_diagram(args):
    """List or create diagrams."""
    client = get_client()
    realm = args.realm or DEFAULT_REALM_ID

    if args.diag_name:
        # Create diagram
        did = client.create_diagram(realm, args.diag_name, args.desc or "")
        if did:
            print(f"  ✅ Created diagram: {did}")
        else:
            print("  ❌ Failed to create diagram.")
    else:
        # List diagrams
        diagrams = client.search_diagrams(realm, args.query or "")
        if diagrams:
            for d in diagrams:
                if isinstance(d, dict):
                    name = d.get("name", "?")
                    did = d.get("id", d.get("uuid", "?"))
                    print(f"  {name} [{did}]")
                else:
                    print(f"  {d}")
        else:
            print("  No diagrams found.")


def cmd_nodes(args):
    """Add nodes to a diagram."""
    client = get_client()
    success = client.add_nodes_to_diagram(
        realm_id=args.realm or DEFAULT_REALM_ID,
        diagram_id=args.diagram,
        node_ids=args.nodes,
    )
    if success:
        print(f"  ✅ Added {len(args.nodes)} nodes to diagram {args.diagram}")
    else:
        print("  ❌ Failed to add nodes.")


def cmd_diff_to_onto(args):
    """Create Onto [UPD] entities from git diff."""
    repo = args.repo or repo_root_from()
    plan = build_plan(repo, args.base, args.head, limit=args.limit)

    if args.format == "json":
        print(json.dumps(to_dict(plan), indent=2, ensure_ascii=False))
        return

    print(render_plan(plan))

    if args.apply:
        result = apply_plan(
            plan,
            realm=args.realm or DEFAULT_REALM_ID,
            diagram_id=args.diagram,
            force_relations=not args.no_relations,
            sleep=args.sleep,
        )
        print("\n## Apply result")
        print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_tools(args):
    """List available MCP tools."""
    client = get_client()
    tools = client.list_tools()
    print(f"  Available MCP tools ({len(tools)}):")
    for t in sorted(tools):
        print(f"    - {t}")


def main():
    parser = argparse.ArgumentParser(prog="ontono", description="Onto knowledge graph CLI")
    parser.add_argument("--realm", help="Realm ID (default: main autolycus realm)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    sub = parser.add_subparsers(dest="command")

    # search
    p_search = sub.add_parser("search", help="Search entities")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--template", help="Template UUID to filter")

    # create
    p_create = sub.add_parser("create", help="Create entity")
    p_create.add_argument("--name", required=True, help="Entity name")
    p_create.add_argument("--desc", help="Entity description")
    p_create.add_argument("--template", help="Template UUID")

    # relation
    p_rel = sub.add_parser("relation", help="Create relation")
    p_rel.add_argument("--source", required=True, help="Source entity ID")
    p_rel.add_argument("--target", required=True, help="Target entity ID")
    p_rel.add_argument("--type", required=True, help="Relation type")

    # diagram
    p_diag = sub.add_parser("diagram", help="List or create diagrams")
    p_diag.add_argument("diag_name", nargs="?", help="Diagram name (omit to list)")
    p_diag.add_argument("--desc", help="Diagram description (with diag_name)")
    p_diag.add_argument("--query", help="Search query (without diag_name)")

    # nodes
    p_nodes = sub.add_parser("nodes", help="Add nodes to diagram")
    p_nodes.add_argument("--diagram", required=True, help="Diagram ID")
    p_nodes.add_argument("--nodes", nargs="+", required=True, help="Entity IDs to add")

    # tools
    sub.add_parser("tools", help="List available MCP tools")

    # diff-to-onto
    p_diff = sub.add_parser("diff-to-onto", help="Parse git diff and plan/apply Onto [UPD] sync")
    p_diff.add_argument("--base", default="upstream/main", help="Base ref, default: upstream/main")
    p_diff.add_argument("--head", default="HEAD", help="Head ref, default: HEAD")
    p_diff.add_argument("--repo", help="Git repo root, default: current git repo")
    p_diff.add_argument("--limit", type=int, help="Limit number of changed files")
    p_diff.add_argument("--apply", action="store_true", help="Create entities/relations in Onto")
    p_diff.add_argument("--diagram", help="Diagram ID to add planned entities as nodes")
    p_diff.add_argument("--no-relations", action="store_true", help="Do not create relations even with --apply")
    p_diff.add_argument("--sleep", type=float, default=0.35, help="Pause between MCP calls, seconds")
    p_diff.add_argument("--format", choices=["text", "json"], default="text", help="Dry-run output format")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        "search": cmd_search,
        "create": cmd_create,
        "relation": cmd_relation,
        "diagram": cmd_diagram,
        "nodes": cmd_nodes,
        "tools": cmd_tools,
        "diff-to-onto": cmd_diff_to_onto,
    }

    handler = cmds.get(args.command)
    if handler:
        handler(args)
    else:
        print(f"Unknown command: {args.command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
