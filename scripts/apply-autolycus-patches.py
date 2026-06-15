#!/usr/bin/env python3
"""
apply-autolycus-patches.py — Применяет Autolycus-патчи после мержа с upstream.

Этот скрипт вызывается автоматически после git merge upstream/main.
Он читает upstream-версию файлов и добавляет наши изменения:

1. VALID_HOOKS — добавить 3 новых хука
2. run_agent.py — добавить вызовы invoke_hook в правильных местах
3. hermes_constants.py — адаптировать путь ~/.autolycus

Использование:
    python3 scripts/apply-autolycus-patches.py
"""
import os
import re
import sys

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def patch_valid_hooks():
    """Добавить новые хуки в VALID_HOOKS."""
    path = os.path.join(REPO_DIR, "hermes_cli", "plugins.py")
    with open(path, "r") as f:
        content = f.read()
    
    new_hooks = '''    # Message persistence hooks — fired by run_agent.py before writing to state.db
    "before_persist_message",
    "before_persist_system_prompt",
    # LLM response hooks — fired after each API response
    "after_llm_response",
    # Agent activity hooks — fired on tool dispatch / message handling
    "post_activity",
'''
    
    # Проверяем что хуки уже добавлены
    if "before_persist_message" in content:
        print(f"  {path}: hooks already added")
        return
    
    # Ищем конец VALID_HOOKS множества
    # Ищем последний элемент перед "}"
    pattern = r'(\s*"post_approval_response",\n)(})'
    replacement = r'\1' + new_hooks + r'\2'
    
    if re.search(pattern, content):
        content = re.sub(pattern, replacement, content)
        with open(path, "w") as f:
            f.write(content)
        print(f"  ✅ {path}: added 3 new hooks to VALID_HOOKS")
    else:
        print(f"  ⚠️ {path}: could not find VALID_HOOKS pattern, manual fix needed")


def patch_run_agent_hooks():
    """Добавить вызовы invoke_hook в run_agent.py."""
    path = os.path.join(REPO_DIR, "run_agent.py")
    with open(path, "r") as f:
        content = f.read()
    
    # Проверяем что хуки уже добавлены
    if "before_persist_message" in content and "post_activity" in content:
        print(f"  {path}: hooks already added")
        return
    
    changes = 0
    
    # 1. before_persist_message — добавить перед сохранением сообщений
    old_persist = '''                # Defence-in-depth: redact credentials from every message
                # content before persistence. Catches PATs / API keys / Bearer
                # tokens that may have leaked into assistant responses, tool
                # output, or user paste. Respects HERMES_REDADACT_SECRETS via
                # redact_sensitive_text — no-op when disabled. (#19798, #19845)
                if "content" in msg:
                    msg = dict(msg)
                    msg["content"] = self._redact_message_content(msg.get("content"))'''
    
    new_persist = '''                # Hook: before_persist_message — plugins can transform/redact
                # message content before persistence. Redactor plugin hooks here
                # to strip credentials (PATs, API keys, Bearer tokens) from
                # messages before they reach state.db.
                from hermes_cli.plugins import invoke_hook as _invoke_hook
                for _hook_result in _invoke_hook(
                    "before_persist_message", agent=self, msg=msg,
                ):
                    if isinstance(_hook_result, dict):
                        msg = _hook_result
                # Fallback: apply built-in secret redaction if no plugin handled it
                if "content" in msg and not getattr(msg, "_redacted", False):
                    msg = dict(msg)
                    msg["content"] = self._redact_message_content(msg.get("content"))'''
    
    if old_persist in content and "before_persist_message" not in content:
        content = content.replace(old_persist, new_persist)
        changes += 1
        print(f"  ✅ {path}: added before_persist_message hook")
    
    # 2. post_activity — заменить прямой вызов kanban heartbeat на хук
    old_activity = '''        if os.environ.get("HERMES_KANBAN_TASK"):
            try:
                from tools.kanban_tools import heartbeat_current_worker_from_env
                heartbeat_current_worker_from_env()
            except Exception:
                # Never let the bridge break the agent loop.  The function
                # already swallows exceptions internally; this outer guard
                # covers import-time failures (kanban_tools unavailable,
                # etc.) on niche deployment surfaces.
                pass'''
    
    new_activity = '''        # Hook: post_activity — plugins can react to agent activity.
        # Kanban plugin hooks here to bridge worker heartbeat to the board.
        from hermes_cli.plugins import invoke_hook as _invoke_hook_pa
        _invoke_hook_pa(
            "post_activity",
            agent=self,
            desc=desc,
            kanban_task=os.environ.get("HERMES_KANBAN_TASK", ""),
        )'''
    
    if old_activity in content and "post_activity" not in content:
        content = content.replace(old_activity, new_activity)
        changes += 1
        print(f"  ✅ {path}: added post_activity hook")
    
    if changes > 0:
        with open(path, "w") as f:
            f.write(content)
    else:
        print(f"  {path}: no changes needed (already patched or pattern not found)")


def main():
    print("=== Apply Autolycus patches ===")
    patch_valid_hooks()
    patch_run_agent_hooks()
    print("\n✅ Done. Run verification:")
    print("  python3 -c \"from hermes_cli.plugins import VALID_HOOKS; print(VALID_HOOKS)\"")
    print("  python3 -c \"from plugins.redactor import register; print('redactor OK')\"")
    print("  python3 -c \"from plugins.credits_notices import register; print('credits OK')\"")
    print("  python3 -c \"from plugins.kanban_heartbeat import register; print('kanban_heartbeat OK')\"")


if __name__ == "__main__":
    main()
