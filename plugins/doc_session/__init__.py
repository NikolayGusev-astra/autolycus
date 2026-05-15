"""
plugins/doc_session — Document Session Plugin.

Создание многостраничных документов через session-based запись по разделам.
Каждый раздел — отдельный tool_call (≤15K токенов), транкейшн невозможен.

Три уровня защиты от write_file для больших документов:
  Level 1 — skill (writing-documents) подсказывает doc_session
  Level 2 — pre_tool_call hook блокирует write_file с контентом >15K
  Level 3 — transform_tool_result добавляет совет после write_file >5K
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from plugins.doc_session import session_manager
from plugins.doc_session import store as doc_store
from plugins.doc_session.template_loader import list_templates

logger = logging.getLogger(__name__)

# Track write_file calls per path for Level 3 repeated-write detection
_write_file_counts: dict[str, int] = {}


def register(ctx) -> None:
    """Register doc_session tools and hooks."""
    try:
        # Tools
        ctx.register_tool(
            name="file_doc_create",
            toolset="doc",
            schema=FILE_DOC_CREATE_SCHEMA,
            handler=_handle_doc_create,
            emoji="📄",
        )
        ctx.register_tool(
            name="file_doc_write",
            toolset="doc",
            schema=FILE_DOC_WRITE_SCHEMA,
            handler=_handle_doc_write,
            emoji="✏️",
        )
        ctx.register_tool(
            name="file_doc_rewrite",
            toolset="doc",
            schema=FILE_DOC_REWRITE_SCHEMA,
            handler=_handle_doc_rewrite,
            emoji="🔄",
        )
        ctx.register_tool(
            name="file_doc_finalize",
            toolset="doc",
            schema=FILE_DOC_FINALIZE_SCHEMA,
            handler=_handle_doc_finalize,
            emoji="✅",
        )
        ctx.register_tool(
            name="file_doc_status",
            toolset="doc",
            schema=FILE_DOC_STATUS_SCHEMA,
            handler=_handle_doc_status,
            emoji="📊",
        )
        ctx.register_tool(
            name="file_doc_resume",
            toolset="doc",
            schema=FILE_DOC_RESUME_SCHEMA,
            handler=_handle_doc_resume,
            emoji="▶️",
        )

        # Hooks: три уровня защиты от write_file для больших документов
        ctx.register_hook("pre_tool_call", _on_pre_tool_call)
        ctx.register_hook("transform_tool_result", _on_transform_tool_result)

        # Cleanup old sessions on startup
        removed = doc_store.cleanup_old()
        if removed:
            logger.info("[doc_session] Cleaned up %d stale sessions", removed)

        logger.info("[doc_session] Registered: 6 tools + 2 hooks")
    except Exception as e:
        logger.critical("[doc_session] Registration FAILED: %s", e)


# ── Schema definitions ──────────────────────────────────────────────────────

FILE_DOC_CREATE_SCHEMA = {
    "name": "file_doc_create",
    "description": "Создать сессию документа с планом разделов. "
                   "Используй вместо write_file для больших документов (>100 строк). "
                   "Укажи path, опционально template и sources.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Путь к итоговому файлу"},
            "template": {"type": "string", "description": "Имя шаблона (quarterly-report, meeting-minutes, research-analysis)"},
            "sources": {"type": "array", "items": {"type": "string"}, "description": "Список исходных файлов для анализа"},
        },
        "required": ["path"],
    },
}

FILE_DOC_WRITE_SCHEMA = {
    "name": "file_doc_write",
    "description": "Написать один раздел документа. "
                   "Для каждого раздела делай отдельный вызов. "
                   "Если не указывать section_id, модель выберет первый незавершённый.",
    "parameters": {
        "type": "object",
        "properties": {
            "session": {"type": "string", "description": "ID сессии (из file_doc_create)"},
            "section": {"type": "string", "description": "ID раздела (из плана сессии). Если не указан, модель выберет сама."},
        },
        "required": ["session"],
    },
}

FILE_DOC_REWRITE_SCHEMA = {
    "name": "file_doc_rewrite",
    "description": "Переписать существующий раздел с новой инструкцией. "
                   "Остальные разделы не трогаются.",
    "parameters": {
        "type": "object",
        "properties": {
            "session": {"type": "string", "description": "ID сессии"},
            "section": {"type": "string", "description": "ID раздела для перезаписи"},
            "instruction": {"type": "string", "description": "Инструкция: что именно изменить"},
        },
        "required": ["session", "section", "instruction"],
    },
}

FILE_DOC_FINALIZE_SCHEMA = {
    "name": "file_doc_finalize",
    "description": "Собрать все разделы в итоговый файл. "
                   "Генерирует TOC, проверяет cross-references, "
                   "пишет .md (и опционально .pdf/.docx через pandoc).",
    "parameters": {
        "type": "object",
        "properties": {
            "session": {"type": "string", "description": "ID сессии"},
            "format": {"type": "string", "enum": ["md", "pdf", "docx"], "description": "Формат вывода", "default": "md"},
        },
        "required": ["session"],
    },
}

FILE_DOC_STATUS_SCHEMA = {
    "name": "file_doc_status",
    "description": "Проверить прогресс документа: сколько разделов готово, сколько осталось.",
    "parameters": {
        "type": "object",
        "properties": {
            "session": {"type": "string", "description": "ID сессии"},
        },
        "required": ["session"],
    },
}

FILE_DOC_RESUME_SCHEMA = {
    "name": "file_doc_resume",
    "description": "Восстановить незавершённую сессию документа после перерыва. "
                   "Ищет сессию по пути файла.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Путь к файлу документа"},
        },
        "required": ["path"],
    },
}


# ── Tool handlers ───────────────────────────────────────────────────────────

def _handle_doc_create(task_id: str, args: dict, **kwargs) -> str:
    path = args.get("path", "")
    template = args.get("template")
    sources = args.get("sources")

    if not path:
        return _error("path is required")

    state = session_manager.create_session(
        path=path,
        template_id=template,
        sources=sources,
    )

    tpl_list = list_templates()
    tpl_hint = f"\nДоступные шаблоны: {', '.join(tpl_list)}" if tpl_list else ""

    plan_str = "\n".join(
        f"  {s.get('title', s['id'])} — {s.get('description', '')}"
        for s in state["plan"]
    )

    return _result(
        f"📄 Сессия документа создана: {state['session_id']}\n"
        f"   Путь: {path}\n"
        f"   План разделов ({len(state['plan'])}):\n{plan_str}"
        f"{tpl_hint}\n\n"
        f"Используй file_doc_write session=\"{state['session_id']}\" "
        f"чтобы написать каждый раздел."
    )


def _handle_doc_write(task_id: str, args: dict, **kwargs) -> str:
    session_id = args.get("session", "")
    section_id = args.get("section")

    if not session_id:
        return _error("session is required")

    state = session_manager.get_session(session_id)
    if state is None:
        return _error(f"Session {session_id} not found. Use file_doc_create first.")

    if not section_id:
        # Auto-select first pending section
        written = set(state["sections"].keys())
        for s in state["plan"]:
            if s["id"] not in written:
                section_id = s["id"]
                break
        if not section_id:
            return _error("All sections are complete. Use file_doc_finalize.")

    section_title = _find_section_title(state, section_id)

    # Return result with section info so the model writes to content via generate
    context = _build_section_context(state, section_id)

    return _result(
        f"✏️ Пиши раздел \"{section_title}\" [id={section_id}]\n\n"
        f"{context}\n\n"
        f"После того как напишешь раздел, "
        f"вызови file_doc_write session=\"{session_id}\" section=\"{section_id}\" "
        f"с контентом в content параметре."
    )


def _build_section_context(state: dict, section_id: str) -> str:
    """Build context for writing a section: template info + adjacent section summaries."""
    parts = []

    # Find section info from plan
    for s in state["plan"]:
        if s["id"] == section_id:
            desc = s.get("description", "")
            if desc:
                parts.append(f"Описание: {desc}")

    # Adjacent section summaries (abbreviated)
    written = state["sections"]
    for s in state["plan"]:
        if s["id"] in written:
            content = written[s["id"]]
            summary = content[:200] + "..." if len(content) > 200 else content
            parts.append(f"\nРаздел «{s.get('title', s['id'])}» (завершён):\n{summary}")
        elif s["id"] == section_id:
            parts.append(f"\n← Текущий раздел «{s.get('title', s['id'])}»")

    return "\n".join(parts)


def _find_section_title(state: dict, section_id: str) -> str:
    for s in state["plan"]:
        if s["id"] == section_id:
            return s.get("title", section_id)
    return section_id


def _handle_doc_rewrite(task_id: str, args: dict, **kwargs) -> str:
    session_id = args.get("session", "")
    section_id = args.get("section", "")
    instruction = args.get("instruction", "")

    if not all([session_id, section_id, instruction]):
        return _error("session, section, and instruction are required")

    state = session_manager.get_session(session_id)
    if state is None:
        return _error(f"Session {session_id} not found")

    old_content = state["sections"].get(section_id, "")
    section_title = _find_section_title(state, section_id)

    return _result(
        f"🔄 Перепиши раздел \"{section_title}\" [id={section_id}]\n\n"
        f"Инструкция: {instruction}\n\n"
        f"Текущее содержание ({len(old_content)} символов):\n"
        f"{old_content[:500]}{'...' if len(old_content) > 500 else ''}\n\n"
        f"После того как напишешь новую версию, "
        f"вызови file_doc_rewrite session=\"{session_id}\" "
        f"section=\"{section_id}\" с content параметром."
    )


def _handle_doc_finalize(task_id: str, args: dict, **kwargs) -> str:
    session_id = args.get("session", "")
    out_format = args.get("format", "md")

    if not session_id:
        return _error("session is required")

    result = session_manager.finalize_session(session_id, format=out_format)

    if result is None:
        return _error("Finalization failed — check session status")

    # Check if result is an error string (starts with error message)
    if result.startswith("Cannot finalize"):
        return _error(result)

    from plugins.doc_session import export
    export_result = export.export(result, out_format)

    return _result(
        f"✅ Документ собран!\n"
        f"   Файл: {result}\n"
        f"   Формат: {out_format}\n"
        f"{export_result}"
    )


def _handle_doc_status(task_id: str, args: dict, **kwargs) -> str:
    session_id = args.get("session", "")
    if not session_id:
        return _error("session is required")

    status = session_manager.get_section_status(session_id)
    if "error" in status:
        return _error(status["error"])

    lines = [f"📊 Прогресс документа: {status['progress']} разделов"]
    for s in status["plan"]:
        icon = "✅" if s["status"] == "complete" else "⬜"
        lines.append(f"  {icon} {s['title']} ({s['words']} слов)")
    return _result("\n".join(lines))


def _handle_doc_resume(task_id: str, args: dict, **kwargs) -> str:
    path = args.get("path", "")
    if not path:
        return _error("path is required")

    state = session_manager.resume_session(path)
    if state is None:
        return _error(f"No in-progress session found for {path}")

    status = session_manager.get_section_status(state["session_id"])
    plan_str = "\n".join(
        f"  {'✅' if s['status'] == 'complete' else '⬜'} {s['title']}"
        for s in status["plan"]
    )

    return _result(
        f"▶️ Сессия восстановлена: {state['session_id']}\n\n"
        f"{plan_str}\n\n"
        f"Продолжай через file_doc_write session=\"{state['session_id']}\""
    )


# ── Hooks: три уровня защиты от write_file ─────────────────────────────────


def _on_pre_tool_call(
    tool_name: str = "",
    args: Optional[dict] = None,
    **kwargs,
) -> Optional[dict]:
    """Level 2: Block write_file with large content."""
    if tool_name != "write_file" or not isinstance(args, dict):
        return None

    content = args.get("content", "")
    if not content:
        return None

    # Only block for document-like files (.md, .txt, .rst, .doc)
    path = args.get("path", "")
    doc_extensions = (".md", ".txt", ".rst", ".doc", ".docx")
    if not any(path.endswith(ext) for ext in doc_extensions):
        # Block for large ANY file if content is really large (>50K chars)
        if len(content) <= 50000:
            return None

    if len(content) > 15000:
        return {
            "action": "block",
            "message": (
                f"⛔ write_file заблокирован: контент слишком большой "
                f"({len(content)} байт). Используй file_doc_create:\n"
                f"1. file_doc_create path='{path}' — создай сессию\n"
                f"2. file_doc_write session='<id>' section='<id>' — "
                f"пиши по одному разделу\n"
                f"3. file_doc_finalize session='<id>' — собери документ"
            ),
        }

    return None


def _on_transform_tool_result(
    tool_name: str = "",
    args: Optional[dict] = None,
    result: Any = None,
    **kwargs,
) -> Optional[str]:
    """Level 3: Add advice after write_file with medium content."""
    if tool_name != "write_file" or not isinstance(args, dict):
        return None

    # Don't modify error results
    if isinstance(result, dict) and result.get("error"):
        return None

    content = args.get("content", "")
    path = args.get("path", "")

    # Track consecutive writes to the same path
    doc_extensions = (".md", ".txt", ".rst")
    if not any(path.endswith(ext) for ext in doc_extensions):
        return None

    _write_file_counts[path] = _write_file_counts.get(path, 0) + 1

    hints = []

    if len(content) > 5000:
        hints.append(
            "💡 Для больших документов используй doc_session plugin: "
            "file_doc_create → file_doc_write → file_doc_finalize. "
            "Каждый раздел — отдельный вызов, нет риска транкейшна."
        )

    if _write_file_counts[path] >= 2:
        hints.append(
            "💡 Ты используешь write_file несколько раз на один файл. "
            "doc_session эффективнее: file_doc_create создаёт план разделов, "
            "file_doc_write пишет каждый отдельно, file_doc_finalize "
            "собирает с авто-TOC."
        )

    if hints:
        suffix = "\n\n" + "\n\n".join(hints)
        if isinstance(result, str):
            return result + suffix
        return str(result) + suffix if result else suffix

    return None


# ── Helpers ─────────────────────────────────────────────────────────────────


def _result(message: str) -> str:
    from tools.registry import tool_result
    return tool_result(message)


def _error(message: str) -> str:
    from tools.registry import tool_error
    return tool_error(message)
