#!/usr/bin/env python3
"""auto_skill.py — CLI entry point for workflow classification.

Usage:
    python3 scripts/auto_skill.py <text query>

Outputs a JSON WorkflowProfile if confidence > 0.5, or {} otherwise.
Exits with code 1 if no query is provided.
"""
import json
import os
import sys

# Ensure scripts/ is on sys.path so we can import sibling modules
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from workflow_classifier import WorkflowClassifier


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/auto_skill.py <query>", file=sys.stderr)
        sys.exit(1)

    query = " ".join(sys.argv[1:])

    clf = WorkflowClassifier()
    clf.register_defaults()
    profile = clf.classify(query)

    if profile is not None and profile.confidence > 0.5:
        output = {
            "name": profile.name,
            "skill": profile.skill,
            "toolsets": profile.toolsets,
            "confidence": profile.confidence,
        }
    else:
        output = {}

    print(json.dumps(output, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
