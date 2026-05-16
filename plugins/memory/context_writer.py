"""
ContextWriter — file-based LLM контекст через raw-wiki.

Сохраняет каждый turn в raw-wiki файл. В памяти — только active_window
последних N turn'ов (N из config.yaml, умолч. 10).

Поиск: rg (portable binary) → grep -l → Python stdlib fallback.
Shutdown: on_session_end пишет последний turn.
"""

from __future__ import annotations
import json, logging, os, shutil, subprocess, sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _get_wiki_dir() -> Path:
    """Определить директорию wiki (та же, что у findings_to_wiki)."""
    from hermes_constants import get_hermes_home
    wiki = get_hermes_home() / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    return wiki


def _load_cw_config() -> dict:
    """Читает конфиг context_writer из config.yaml."""
    try:
        from hermes_cli.config import cfg_get
        root = cfg_get("plugins", "context_writer", default={})
        if isinstance(root, dict):
            return root
    except Exception:
        pass
    return {}


def _find_rg() -> str | None:
    """Ищет rg: plugins/tacops → portable/bin/ → PATH → ~/bin/ → venv/bin/."""
    # 1. tacops (portable toolchain)
    try:
        from plugins.tacops import find_tool as _tacops_find
        rg = _tacops_find("rg")
        if rg and rg != "rg" and Path(rg).is_file():
            return rg
    except Exception:
        pass

    # 2. portable/bin/ (если tacops не загружен, но portable есть)
    portable_candidates = [
        Path.cwd() / "portable" / "bin" / "rg",
        Path(__file__).resolve().parent.parent.parent / "portable" / "bin" / "rg",
        Path.home() / ".autolycus" / "portable" / "bin" / "rg",
    ]
    for p in portable_candidates:
        if p.is_file():
            return str(p)

    # 3. PATH
    rg = shutil.which("rg")
    if rg:
        return rg

    # 4. Распространённые portable locations
    candidates = [
        os.path.expanduser("~/bin/rg"),
        os.path.expanduser("~/.local/bin/rg"),
        os.path.expanduser("~/ripgrep/rg"),
        str(Path(sys.prefix) / "bin" / "rg"),
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _format_turn(turn_number: int, user_msg: str, assistant_msg: str,
                 tools: list[dict] | None = None) -> str:
    """Форматировать один turn для записи в файл."""
    lines = [
        f"## Turn {turn_number}",
        f"**Time:** {datetime.now().isoformat()[:19]}",
        "",
        "### User",
        user_msg,
        "",
        "### Assistant",
        assistant_msg,
    ]
    if tools:
        lines.extend(["", "### Tools"])
        for t in tools[-5:]:
            name = t.get("name", "?")
            result = str(t.get("result", ""))[:200]
            lines.append(f"- `{name}`: {result}")
    return "\n".join(lines)


class ContextWriter:
    """Пишет turn'ы в raw-wiki, управляет active_window в памяти."""

    def __init__(self, wiki_dir: str | Path | None = None,
                 window_size: int | None = None):
        self.wiki_dir = Path(wiki_dir) if wiki_dir else _get_wiki_dir()

        # window_size: config.yaml → kwarg → default 10
        cfg = _load_cw_config()
        self.window_size = window_size or int(cfg.get("window_size", 10))

        self._active_windows: dict[str, list[int]] = {}
        self._rg_path = _find_rg()
        self._rebuild_windows()

        logger.info(
            "[ContextWriter] initialized: wiki=%s, window=%d, rg=%s",
            self.wiki_dir, self.window_size,
            self._rg_path or "not-found (fallback=grep)",
        )

    # ── Window rebuild ──────────────────────────────────────────────────

    def _rebuild_windows(self) -> None:
        """Scan existing turn files on disk to restore active_windows."""
        ctx_dir = self.wiki_dir / "raw" / "context"
        if not ctx_dir.exists():
            return
        for session_dir in sorted(ctx_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            turn_files = sorted(session_dir.glob("turn_*.md"))
            if not turn_files:
                continue
            turn_nums = []
            for f in turn_files:
                try:
                    turn_num = int(f.stem.split("_")[1])
                    turn_nums.append(turn_num)
                except (IndexError, ValueError):
                    continue
            if turn_nums:
                turn_nums.sort()
                sid = session_dir.name
                self._active_windows[sid] = turn_nums[-self.window_size:]

    # ── Paths ───────────────────────────────────────────────────────────

    @property
    def _context_dir(self) -> Path:
        d = self.wiki_dir / "raw" / "context"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _session_dir(self, session_id: str) -> Path:
        d = self._context_dir / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Sync ────────────────────────────────────────────────────────────

    def sync_turn(self, session_id: str, turn_number: int,
                  user_msg: str, assistant_msg: str,
                  tools: list[dict] | None = None,
                  metadata: dict | None = None) -> None:
        content = _format_turn(turn_number, user_msg, assistant_msg, tools)
        if metadata:
            meta_str = json.dumps(metadata, ensure_ascii=False)
            content += f"\n\n<!-- metadata: {meta_str} -->\n"

        turn_file = self._session_dir(session_id) / f"turn_{turn_number:04d}.md"
        turn_file.write_text(content)

        if session_id not in self._active_windows:
            self._active_windows[session_id] = []
        window = self._active_windows[session_id]
        window.append(turn_number)
        while len(window) > self.window_size:
            window.pop(0)

        logger.debug("[ContextWriter] turn %d written to %s (window: %d/%d)",
                     turn_number, turn_file, len(window), self.window_size)

    # ── Active context ─────────────────────────────────────────────────

    def get_active_context(self, session_id: str) -> list[str]:
        window = self._active_windows.get(session_id, [])
        session_dir = self._session_dir(session_id)
        result = []
        for turn_num in window[-self.window_size:]:
            turn_file = session_dir / f"turn_{turn_num:04d}.md"
            if turn_file.exists():
                result.append(turn_file.read_text())
        return result

    # ── Search (rg → grep → Python stdlib) ─────────────────────────────

    def search_context(self, session_id: str, query: str,
                       max_results: int = 5) -> list[dict]:
        """Поиск по контексту сессии. Пробует rg, затем grep, затем stdlib."""
        session_dir = self._session_dir(session_id)
        turn_files = sorted(session_dir.glob("turn_*.md"))
        if not turn_files:
            return []

        # Try rg first
        if self._rg_path:
            try:
                result = subprocess.run(
                    [self._rg_path, "-l", query, str(session_dir)],
                    capture_output=True, text=True, timeout=10,
                )
                if result.stdout.strip():
                    return self._parse_search_results(
                        result.stdout.strip().split("\n")[:max_results], max_results
                    )
            except (subprocess.TimeoutExpired, OSError):
                pass  # fall through

        # Try grep
        try:
            result = subprocess.run(
                ["grep", "-rl", query, str(session_dir)],
                capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                return self._parse_search_results(
                    result.stdout.strip().split("\n")[:max_results], max_results
                )
        except (subprocess.TimeoutExpired, OSError):
            pass  # fall through

        # Python stdlib fallback
        return self._search_python(turn_files, query, max_results)

    def _search_python(self, turn_files: list[Path], query: str,
                       max_results: int) -> list[dict]:
        """Чистый Python fallback — grep по содержимому файлов."""
        results = []
        for f in turn_files:
            if len(results) >= max_results:
                break
            try:
                content = f.read_text()
                if query.lower() in content.lower():
                    turn_num = int(f.stem.split("_")[1])
                    results.append({
                        "turn": turn_num,
                        "path": str(f),
                        "preview": content[:200],
                    })
            except (IndexError, ValueError, OSError):
                continue
        return results

    def _parse_search_results(self, raw_files: list[str],
                              max_results: int) -> list[dict]:
        """Парсит имена файлов от rg/grep в структурированный результат."""
        results = []
        for f in raw_files:
            if len(results) >= max_results:
                break
            try:
                fp = Path(f)
                turn_num = int(fp.stem.split("_")[1])
                preview = fp.read_text()[:200]
                results.append({
                    "turn": turn_num,
                    "path": str(fp),
                    "preview": preview,
                })
            except (IndexError, ValueError, OSError):
                continue
        return results

    # ── Summary ─────────────────────────────────────────────────────────

    def get_summary(self, session_id: str) -> dict:
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return {"turns": 0, "files": 0, "size_kb": 0}
        files = sorted(session_dir.glob("turn_*.md"))
        total_size = sum(f.stat().st_size for f in files)
        return {
            "turns": len(self._active_windows.get(session_id, [])),
            "files": len(files),
            "archived_turns": len(files) - len(self._active_windows.get(session_id, [])),
            "size_kb": total_size // 1024,
        }

    # ── Lifecycle ───────────────────────────────────────────────────────

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """Записать последний turn при завершении сессии.

        Вызывается из shutdown_memory_provider() в run_agent.py.
        """
        if not messages:
            return
        try:
            last_user = ""
            last_asst = ""
            for msg in reversed(messages):
                role = msg.get("role", "")
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        p.get("text", "") for p in content if isinstance(p, dict)
                    )
                content = str(content or "").strip()
                if role == "assistant" and not last_asst:
                    last_asst = content[:2000]
                elif role == "user" and not last_user:
                    last_user = content[:1000]
                if last_user and last_asst:
                    break
            if last_user or last_asst:
                turn = self._active_windows.get(
                    list(self._active_windows.keys())[-1]
                    if self._active_windows else "",
                    [],
                )
                next_turn = (turn[-1] + 1) if turn else 0
                self.sync_turn(
                    session_id=list(self._active_windows.keys())[-1] or "end",
                    turn_number=next_turn,
                    user_msg=last_user,
                    assistant_msg=f"[end-of-session] {last_asst}",
                )
        except Exception as e:
            logger.debug("[ContextWriter] on_session_end: %s", e)

    def shutdown(self) -> None:
        """Clean shutdown placeholder."""
        logger.info("[ContextWriter] shut down")
