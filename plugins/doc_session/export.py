"""
plugins/doc_session/export — Export document to various formats.

Wraps pandoc for PDF/docx output with graceful fallback to .md.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def export(filepath: str, target_format: str = "md") -> str:
    """Export a .md file to the target format.

    Args:
        filepath: Path to the .md file.
        target_format: 'md', 'pdf', or 'docx'.

    Returns:
        Human-readable status message.
    """
    if target_format == "md":
        return "  Формат: Markdown (базовый)"

    source = Path(filepath)
    if not source.exists():
        return "  ⚠️ Исходный файл не найден"

    if not shutil.which("pandoc"):
        return (
            "  ⚠️ Pandoc не найден. Установи: apt install pandoc\n"
            "  Файл сохранён как .md"
        )

    try:
        output = source.with_suffix(f".{target_format}")
        cmd = [
            "pandoc",
            str(source),
            "-o", str(output),
            "--from", "markdown",
            "--to", target_format,
        ]
        if target_format == "pdf":
            # Try common PDF engines
            for engine in ["pdflatex", "xelatex", "lualatex", "wkhtmltopdf"]:
                if shutil.which(engine):
                    cmd.extend(["--pdf-engine", engine])
                    break
            else:
                return (
                    "  ⚠️ PDF engine не найден. Pandoc установлен, но нужен "
                    "latex или wkhtmltopdf.\n"
                    "  Файл сохранён как .md"
                )

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.warning("Pandoc failed: %s", result.stderr)
            return f"  ⚠️ Pandoc ошибка: {result.stderr[:200]}"

        return f"  Экспортировано: {output}"

    except FileNotFoundError:
        return "  ⚠️ Pandoc не найден. Файл сохранён как .md"
    except subprocess.TimeoutExpired:
        return "  ⚠️ Pandoc превысил таймаут (60с). Файл сохранён как .md"
    except Exception as e:
        logger.warning("Export failed: %s", e)
        return f"  ⚠️ Ошибка экспорта: {e}"
