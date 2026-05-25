"""Tests for RTK-CK PatternDetector — sequence-level pattern detection.

Detects:
- REDUNDANT_READS: 3+ identical read_file calls in N turns
- STALLED_SESSION: 3+ cycles of tool→error→tool→error
"""
from __future__ import annotations

import pytest

from plugins.rtk.pattern import Signal


def _user_msg(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant_tool_call(name: str, path: str = "/etc/hosts") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": name, "arguments": f'{{"path":"{path}"}}'}
        }]
    }


def _tool_result(content: str, name: str = "read_file", error: bool = False) -> dict:
    return {
        "role": "tool",
        "content": content,
        "tool_call_id": "call_1",
        "name": name,
    }


# ---------------------------------------------------------------------------
# PatternDetector tests
# ---------------------------------------------------------------------------


class TestRedundantReads:
    """REDUNDANT_READS: agent reads the same file 3+ times."""

    def test_single_read_no_signal(self):
        """Single read_file → no signal."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("read hosts"),
            _assistant_tool_call("read_file", "/etc/hosts"),
            _tool_result("127.0.0.1 localhost\n..."),
        ]
        config = {"redundant_read_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) == 0

    def test_two_reads_same_file_no_signal(self):
        """Two reads of same file → no signal (below threshold of 3)."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("read hosts"),
            _assistant_tool_call("read_file", "/etc/hosts"),
            _tool_result("127.0.0.1 localhost"),
            _user_msg("read hosts again"),
            _assistant_tool_call("read_file", "/etc/hosts"),
            _tool_result("127.0.0.1 localhost"),
        ]
        config = {"redundant_read_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) == 0

    def test_three_reads_same_file_redunant(self):
        """Three reads of same file → REDUNDANT_READS warn."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("read hosts"),
            _assistant_tool_call("read_file", "/etc/hosts"),
            _tool_result("127.0.0.1 localhost"),
            _user_msg("read hosts again"),
            _assistant_tool_call("read_file", "/etc/hosts"),
            _tool_result("127.0.0.1 localhost"),
            _user_msg("read hosts again"),
            _assistant_tool_call("read_file", "/etc/hosts"),
            _tool_result("127.0.0.1 localhost"),
        ]
        config = {"redundant_read_threshold": 3}
        signals = PatternDetector.detect(msgs, config)

        assert len(signals) >= 1
        redun = next((s for s in signals if s.code == "REDUNDANT_READS"), None)
        assert redun is not None
        assert redun.severity == "warn"
        assert "/etc/hosts" in redun.message

    def test_three_reads_different_files_no_signal(self):
        """Three reads of different files → not redundant."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("read a"),
            _assistant_tool_call("read_file", "/etc/hosts"),
            _tool_result("127.0.0.1 localhost"),
            _user_msg("read b"),
            _assistant_tool_call("read_file", "/etc/resolv.conf"),
            _tool_result("nameserver 8.8.8.8"),
            _user_msg("read c"),
            _assistant_tool_call("read_file", "/etc/fstab"),
            _tool_result("UUID=... / ext4"),
        ]
        config = {"redundant_read_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) == 0

    def test_mixed_calls_with_one_redundant(self):
        """Mix of tools, one file read 3× → detected."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("read hosts"),
            _assistant_tool_call("read_file", "/etc/hosts"),
            _tool_result("127.0.0.1 localhost"),
            _user_msg("search config"),
            _assistant_tool_call("search_files", "."),
            _tool_result("result: none"),
            _user_msg("read hosts again"),
            _assistant_tool_call("read_file", "/etc/hosts"),
            _tool_result("127.0.0.1 localhost"),
            _user_msg("read hosts again"),
            _assistant_tool_call("read_file", "/etc/hosts"),
            _tool_result("127.0.0.1 localhost"),
        ]
        config = {"redundant_read_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) >= 1
        assert any(s.code == "REDUNDANT_READS" for s in signals)


class TestStalledSession:
    """STALLED_SESSION: tool→error→tool→error cycles."""

    def test_no_errors_no_signal(self):
        """Clean session → no signal."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("do x"),
            _assistant_tool_call("read_file"),
            _tool_result("ok"),
            _user_msg("do y"),
            _assistant_tool_call("write_file"),
            _tool_result("done"),
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) == 0

    def test_single_error_no_signal(self):
        """One error in a tool → no signal."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("connect"),
            _assistant_tool_call("terminal"),
            _tool_result("Error: timeout", name="terminal", error=True),
            _user_msg("try again"),
            _assistant_tool_call("terminal"),
            _tool_result("connected", name="terminal"),
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) == 0

    def test_three_error_cycles_stalled(self):
        """3+ tool→error cycles → STALLED_SESSION critical."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("connect"),
            _assistant_tool_call("terminal"),
            _tool_result("Error: timeout", name="terminal", error=True),
            _user_msg("try again"),
            _assistant_tool_call("terminal"),
            _tool_result("Error: timeout", name="terminal", error=True),
            _user_msg("try again"),
            _assistant_tool_call("terminal"),
            _tool_result("Error: timeout", name="terminal", error=True),
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)

        assert len(signals) >= 1
        stalled = next((s for s in signals if s.code == "STALLED_SESSION"), None)
        assert stalled is not None
        assert stalled.severity == "critical"
        assert stalled.should_halt is True

    def test_stalled_with_mixed_tools(self):
        """Errors across different tools → still stalled."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("deploy"),
            _assistant_tool_call("terminal"),
            _tool_result("Error: timeout", name="terminal", error=True),
            _user_msg("try web"),
            _assistant_tool_call("web_search"),
            _tool_result("Error: 403", name="web_search", error=True),
            _user_msg("try api"),
            _assistant_tool_call("execute_code"),
            _tool_result("Error: import failed", name="execute_code", error=True),
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)

        assert len(signals) >= 1
        stalled = next((s for s in signals if s.code == "STALLED_SESSION"), None)
        assert stalled is not None
        # RBE-like: meta-failure across tools, not same-tool loops
        assert stalled.code == "STALLED_SESSION"

    def test_stalled_then_success_no_signal(self):
        """Errors then success → not stalled (broken cycle)."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("connect"),
            _assistant_tool_call("terminal"),
            _tool_result("Error: timeout", name="terminal", error=True),
            _user_msg("connect again"),
            _assistant_tool_call("terminal"),
            _tool_result("Connected!", name="terminal"),  # success
            _user_msg("deploy"),
            _assistant_tool_call("terminal"),
            _tool_result("Error: deploy failed", name="terminal", error=True),
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        # Only 1 error cycle (the 3rd call succeeded), then 1 more error
        # nope — let's check logic: 3 errors total but 1 success breaks the chain
        # Actually errors at indices 2, 8 → only 2 consecutive error cycles
        assert len(signals) == 0


class TestEmptyMessages:
    """Edge cases with empty/minimal messages."""

    def test_empty_list_no_signals(self):
        """Empty messages → empty signals list."""
        from plugins.rtk_ck.patterns import PatternDetector

        signals = PatternDetector.detect([], {})
        assert len(signals) == 0

    def test_single_user_msg_no_signals(self):
        """Single user message → empty signals list."""
        from plugins.rtk_ck.patterns import PatternDetector

        signals = PatternDetector.detect(
            [_user_msg("hello")], {}
        )
        assert len(signals) == 0


class TestFalsePositiveErrors:
    """Tool results that mention 'error' but are NOT actual failures.

    Regression tests for the Ozon-case fix: 'error' in JSON null fields,
    exit_code 0, and normal terminal output must not count as errors.
    """

    def test_json_error_null_not_counted(self):
        """"error": null in tool result → not an error."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("run command"),
            _assistant_tool_call("terminal"),
            _tool_result('{"exit_code": 0, "error": null, "output": "done"}', name="terminal"),
            _user_msg("run again"),
            _assistant_tool_call("terminal"),
            _tool_result('{"exit_code": 0, "error": null, "output": "ok"}', name="terminal"),
            _user_msg("run third"),
            _assistant_tool_call("terminal"),
            _tool_result('{"exit_code": 0, "error": null, "output": "fine"}', name="terminal"),
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) == 0

    def test_exit_code_zero_not_counted(self):
        """exit_code: 0 → not an error even if 'error' appears elsewhere."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("exec"),
            _assistant_tool_call("execute_code"),
            _tool_result('{"exit_code": 0, "stderr": "", "stdout": "hello"}', name="execute_code"),
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) == 0

    def test_exit_code_nonzero_is_error(self):
        """exit_code: 1 → IS an error."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("exec"),
            _assistant_tool_call("terminal"),
            _tool_result('{"exit_code": 1, "stderr": "command not found"}', name="terminal"),
            _user_msg("exec 2"),
            _assistant_tool_call("terminal"),
            _tool_result('{"exit_code": 127, "stderr": "no such file"}', name="terminal"),
            _user_msg("exec 3"),
            _assistant_tool_call("terminal"),
            _tool_result('{"exit_code": 2, "stderr": "syntax error"}', name="terminal"),
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) >= 1
        assert any(s.code == "STALLED_SESSION" for s in signals)

    def test_stderr_empty_not_error(self):
        """stderr: '' (empty) → not an error."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("exec"),
            _assistant_tool_call("terminal"),
            _tool_result("output line\nstderr: ''\nexit_code: 0", name="terminal"),
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) == 0

    def test_real_timeout_still_detected(self):
        """Real 'timeout' message → still detected as error."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("req"),
            _assistant_tool_call("web_search"),
            _tool_result("Timeout after 30s", name="web_search"),
            _user_msg("req 2"),
            _assistant_tool_call("web_search"),
            _tool_result("Timeout after 30s", name="web_search"),
            _user_msg("req 3"),
            _assistant_tool_call("web_search"),
            _tool_result("Timeout after 30s", name="web_search"),
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) >= 1
        assert any(s.code == "STALLED_SESSION" for s in signals)

    def test_traceback_still_detected(self):
        """Traceback → still detected as error."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("run"),
            _assistant_tool_call("execute_code"),
            _tool_result("Traceback (most recent call last):\n  File 'x.py'\nSyntaxError", name="execute_code"),
            _user_msg("run 2"),
            _assistant_tool_call("execute_code"),
            _tool_result("Traceback (most recent call last):\nValueError: bad", name="execute_code"),
            _user_msg("run 3"),
            _assistant_tool_call("execute_code"),
            _tool_result("Error: something broke", name="execute_code"),
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) >= 1
        assert any(s.code == "STALLED_SESSION" for s in signals)

    def test_denied_forbidden_still_detected(self):
        """403 denied / forbidden → still detected."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("fetch"),
            _assistant_tool_call("web_search"),
            _tool_result("403 Forbidden", name="web_search"),
            _user_msg("fetch 2"),
            _assistant_tool_call("web_search"),
            _tool_result("Access denied", name="web_search"),
            _user_msg("fetch 3"),
            _assistant_tool_call("web_search"),
            _tool_result("403 Forbidden", name="web_search"),
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) >= 1
        assert any(s.code == "STALLED_SESSION" for s in signals)

    def test_real_error_still_detected(self):
        """Real error messages (not JSON null) → detected."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("exec"),
            _assistant_tool_call("terminal"),
            _tool_result("error: command not found", name="terminal"),
            _user_msg("exec 2"),
            _assistant_tool_call("terminal"),
            _tool_result("error: permission denied", name="terminal"),
            _user_msg("exec 3"),
            _assistant_tool_call("terminal"),
            _tool_result("error: connection refused", name="terminal"),
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        assert len(signals) >= 1
        assert any(s.code == "STALLED_SESSION" for s in signals)

    def test_mixed_real_and_false_positive(self):
        """Mix of real errors and false positives — only real count."""
        from plugins.rtk_ck.patterns import PatternDetector

        msgs = [
            _user_msg("exec 1"),
            _assistant_tool_call("terminal"),
            _tool_result('{"exit_code": 0, "error": null}', name="terminal"),  # false positive
            _user_msg("exec 2"),
            _assistant_tool_call("terminal"),
            _tool_result("error: timeout", name="terminal"),  # real error
            _user_msg("exec 3"),
            _assistant_tool_call("terminal"),
            _tool_result('{"exit_code": 0, "error": null}', name="terminal"),  # false positive (resets counter)
            _user_msg("exec 4"),
            _assistant_tool_call("terminal"),
            _tool_result("error: failed", name="terminal"),  # real error
        ]
        config = {"stalled_threshold": 3}
        signals = PatternDetector.detect(msgs, config)
        # Only 2 real errors, not consecutive → no STALLED_SESSION
        assert len(signals) == 0