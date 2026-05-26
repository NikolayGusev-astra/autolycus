"""Unit tests for SBL (System Boundary Layer) plugin.

Tests are hermetic — they mock system-level calls (systemctl, ss, /proc)
that require root or real services. Pure logic functions like path
classification, terminal command parsing, and output formatting are
tested against the real implementation.
"""

import copy
import json
from pathlib import Path
from unittest.mock import ANY, MagicMock, PropertyMock, patch

import pytest

from plugins.sbl import (
    ServiceMap,
    _change_log,
    _classify_path,
    _classify_terminal_cmd,
    _ensure_snapshot_dir,
    _format_deps,
    _handle_sbl_snapshot,
    _has_snapshot,
    _learn_change,
    _lookup_dependencies,
    _normalize_to_path,
    _on_pre_tool_call,
    _on_transform_tool_result,
    _service_map,
    _snapshot_taken,
    _take_snapshot,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_sbl_state():
    """Reset SBL global state before every test.

    The global conftest resets many module-level buckets but doesn't know
    about SBL. We restore the module-level singletons so tests don't leak
    snapshots or change logs across test boundaries.
    """
    import plugins.sbl as _sbl_mod

    _sbl_mod._snapshot_taken = False
    _sbl_mod._service_map = ServiceMap()
    _sbl_mod._change_log.clear()
    _sbl_mod._SNAPSHOT_DIR = None


@pytest.fixture
def no_snapshot(monkeypatch):
    """Prevent _has_snapshot from finding any on-disk snapshot."""
    import plugins.sbl as _sbl_mod

    monkeypatch.setattr(_sbl_mod, "_has_snapshot", lambda: False)
    return


@pytest.fixture
def populated_service_map():
    """Build a realistic ServiceMap with known services/configs."""
    sm = ServiceMap(
        services={
            "nginx": {
                "ports": ["80", "443"],
                "configs": ["/etc/nginx/"],
                "type": "systemd",
            },
            "ssh": {
                "ports": ["22"],
                "configs": ["/etc/ssh/"],
                "type": "systemd",
            },
            "docker": {
                "ports": [],
                "configs": ["/etc/docker/"],
                "type": "systemd",
            },
        },
        file_owners={
            "/etc/nginx/": ["nginx"],
            "/etc/nginx/nginx.conf": ["nginx"],
            "/etc/ssh/": ["ssh"],
            "/etc/ssh/sshd_config": ["ssh"],
            "/etc/hosts": ["networking"],
            "/etc/docker/": ["docker"],
        },
        port_owners={
            "80": {"nginx"},
            "443": {"nginx"},
            "22": {"ssh"},
        },
    )
    return sm


# ═══════════════════════════════════════════════════════════════════════════
# 1. FHS Classification
# ═══════════════════════════════════════════════════════════════════════════

class TestClassifyPath:
    """_classify_path — pure FHS-based path classification."""

    @pytest.mark.parametrize("path", [
        "/etc/nginx/nginx.conf",
        "/opt/hermes/test",
        "/usr/local/etc/xray/config.json",
        "/var/log/syslog",
        "/var/lib/postgresql/data",
        "/var/www/html/index.html",
        "/boot/grub/grub.cfg",
        "/bin/bash",
        "/sbin/init",
        "/lib/x86_64-linux-gnu/libc.so",
        "/lib64/ld-linux-x86-64.so.2",
        "/snap/core/current/",
        "/usr/local/etc/stalwart/config.toml",
    ])
    def test_system(self, path):
        assert _classify_path(path) == "SYSTEM", f"{path} should be SYSTEM"

    @pytest.mark.parametrize("path", [
        "/home/user/test.txt",
        "/tmp/test",
        "/var/tmp/cache",
        "/root/.bashrc",
        "/home/user/project/config.yaml",
    ])
    def test_user(self, path):
        assert _classify_path(path) == "USER", f"{path} should be USER"

    @pytest.mark.parametrize("path", [
        "/unknown/path/lib.so",
        "/weird/location/file",
        "/custom/app/config.yml",
        "/data/files/db.sql",
    ])
    def test_unknown(self, path):
        assert _classify_path(path) == "UNKNOWN", f"{path} should be UNKNOWN"

    def test_empty_string(self):
        assert _classify_path("") == "UNKNOWN"

    def test_none(self):
        assert _classify_path(None) == "UNKNOWN"  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════
# 2. Path / Command Normalization
# ═══════════════════════════════════════════════════════════════════════════

class TestNormalizeToPath:
    """_normalize_to_path — extract path + classification from tool args."""

    def test_write_file_system(self):
        path, cls = _normalize_to_path("write_file", {
            "path": "/etc/nginx/nginx.conf", "content": "data",
        })
        assert path == "/etc/nginx/nginx.conf"
        assert cls == "SYSTEM"

    def test_write_file_user(self):
        path, cls = _normalize_to_path("write_file", {
            "path": "/home/user/test.txt", "content": "data",
        })
        assert path == "/home/user/test.txt"
        assert cls == "USER"

    def test_write_file_unknown(self):
        path, cls = _normalize_to_path("write_file", {
            "path": "/custom/path/lib.so", "content": "data",
        })
        assert path == "/custom/path/lib.so"
        assert cls == "UNKNOWN"

    def test_patch_system(self):
        path, cls = _normalize_to_path("patch", {
            "path": "/etc/nginx/nginx.conf",
        })
        assert path == "/etc/nginx/nginx.conf"
        assert cls == "SYSTEM"

    def test_terminal_delegates(self):
        """Terminal commands are handled by _classify_terminal_cmd."""
        path, cls = _normalize_to_path("terminal", {
            "command": "systemctl restart nginx",
        })
        assert cls == "SYSTEM"

    def test_terminal_echo_redirect(self):
        path, cls = _normalize_to_path("terminal", {
            "command": "echo 'test' >> /etc/hosts",
        })
        assert path == "/etc/hosts"
        assert cls == "SYSTEM"

    def test_non_write_tool_returns_empty(self):
        path, cls = _normalize_to_path("read_file", {
            "path": "/etc/nginx/nginx.conf",
        })
        assert path == ""
        assert cls == "UNKNOWN"

    def test_terminal_empty_command(self):
        path, cls = _normalize_to_path("terminal", {"command": ""})
        assert path == ""
        assert cls == "UNKNOWN"

    def test_terminal_no_args(self):
        path, cls = _normalize_to_path("terminal", {})
        assert path == ""
        assert cls == "UNKNOWN"


class TestClassifyTerminalCmd:
    """_classify_terminal_cmd — extract path + class from shell commands."""

    def test_systemctl_service_name(self):
        path, cls = _classify_terminal_cmd("systemctl restart nginx")
        assert path == "nginx"
        assert cls == "SYSTEM"

    def test_systemctl_with_dash(self):
        path, cls = _classify_terminal_cmd("systemctl stop fail2ban")
        assert path == "fail2ban"
        assert cls == "SYSTEM"

    def test_systemctl_simple(self):
        path, cls = _classify_terminal_cmd("systemctl reload nginx")
        assert cls == "SYSTEM"

    def test_sudo_systemctl(self):
        path, cls = _classify_terminal_cmd("sudo systemctl restart docker")
        assert cls == "SYSTEM"

    def test_echo_redirect_system(self):
        path, cls = _classify_terminal_cmd("echo '127.0.0.1 test' >> /etc/hosts")
        assert path == "/etc/hosts"
        assert cls == "SYSTEM"

    def test_echo_redirect_user(self):
        path, cls = _classify_terminal_cmd("echo 'hello' >> /home/user/test.txt")
        assert path == "/home/user/test.txt"
        assert cls == "USER"

    def test_cp_to_system(self):
        path, cls = _classify_terminal_cmd("sudo cp backup.conf /etc/nginx/nginx.conf")
        assert path == "/etc/nginx/nginx.conf"
        assert cls == "SYSTEM"

    def test_mv_to_system(self):
        path, cls = _classify_terminal_cmd("sudo mv /tmp/config /etc/stalwart/config.toml")
        assert path == "/etc/stalwart/config.toml"
        assert cls == "SYSTEM"

    def test_rm_system_file(self):
        path, cls = _classify_terminal_cmd("sudo rm /var/log/nginx/error.log")
        assert path == "/var/log/nginx/error.log"
        assert cls == "SYSTEM"

    def test_sed_i_system_file(self):
        path, cls = _classify_terminal_cmd("sudo sed -i 's/old/new/' /etc/ssh/sshd_config")
        assert path == "/etc/ssh/sshd_config"
        assert cls == "SYSTEM"

    def test_nginx_config_test(self):
        path, cls = _classify_terminal_cmd("nginx -t")
        assert path == "nginx-config-test"
        assert cls == "SYSTEM"

    def test_simple_read_no_match(self):
        path, cls = _classify_terminal_cmd("cat /etc/nginx/nginx.conf")
        assert path == ""
        assert cls == "UNKNOWN"

    def test_ls_no_match(self):
        path, cls = _classify_terminal_cmd("ls -la /etc/")
        assert path == ""
        assert cls == "UNKNOWN"

    def test_empty_command(self):
        path, cls = _classify_terminal_cmd("")
        assert path == ""
        assert cls == "UNKNOWN"

    def test_known_service_name_shortcut(self):
        """Known service names used as bare commands are treated as SYSTEM."""
        for svc in ("nginx", "xray", "stalwart", "docker"):
            path, cls = _classify_terminal_cmd(f"sudo systemctl restart {svc}")
            assert cls == "SYSTEM", f"systemctl restart {svc} should be SYSTEM"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Dependency Formatting
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatDeps:
    """_format_deps — human-readable dependency listing."""

    def test_empty(self):
        assert _format_deps([]) == ""

    def test_single_service_no_ports(self):
        result = _format_deps([
            {"service": "nginx", "through": "/etc/nginx/", "ports": [], "type": "systemd"},
        ])
        assert "nginx" in result
        assert "systemd" in result
        assert "/etc/nginx/" in result

    def test_single_service_with_ports(self):
        result = _format_deps([
            {"service": "nginx", "through": "/etc/nginx/nginx.conf", "ports": ["80", "443"], "type": "systemd"},
        ])
        assert "nginx" in result
        assert "ports: 80, 443" in result
        assert "systemd" in result

    def test_multiple_services(self):
        result = _format_deps([
            {"service": "nginx", "through": "/etc/nginx/nginx.conf", "ports": ["80"], "type": "systemd"},
            {"service": "docker", "through": "/etc/docker/daemon.json", "ports": [], "type": "systemd"},
        ])
        assert "nginx" in result
        assert "docker" in result
        assert "port: 80" in result  # singular — only one port

    def test_unknown_type(self):
        result = _format_deps([
            {"service": "custom-app", "through": "/opt/app/config.yml", "ports": [], "type": "unknown"},
        ])
        assert "custom-app" in result
        assert "unknown" in result


# ═══════════════════════════════════════════════════════════════════════════
# 4. Dependency Lookup
# ═══════════════════════════════════════════════════════════════════════════

class TestLookupDependencies:
    """_lookup_dependencies — match path against file_owners map."""

    def test_exact_path_match(self, populated_service_map):
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        deps = _lookup_dependencies("/etc/nginx/nginx.conf")
        assert len(deps) == 1
        assert deps[0]["service"] == "nginx"
        assert deps[0]["ports"] == ["80", "443"]

    def test_directory_prefix_match(self, populated_service_map):
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        deps = _lookup_dependencies("/etc/nginx/sites-enabled/default")
        assert len(deps) >= 1
        assert deps[0]["service"] == "nginx"

    def test_no_match(self, populated_service_map):
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        deps = _lookup_dependencies("/nonexistent/path")
        assert deps == []

    def test_empty_path(self, populated_service_map):
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        deps = _lookup_dependencies("")
        assert deps == []

    def test_etc_hosts_default(self, populated_service_map):
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        deps = _lookup_dependencies("/etc/hosts")
        assert len(deps) == 1
        assert deps[0]["service"] == "networking"

    def test_deduplication(self, populated_service_map):
        """Multiple config paths mapping to the same service deduplicate."""
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        deps = _lookup_dependencies("/etc/nginx/")
        names = [d["service"] for d in deps]
        assert names.count("nginx") == 1


# ═══════════════════════════════════════════════════════════════════════════
# 5. Pre-Tool-Call Hook
# ═══════════════════════════════════════════════════════════════════════════

class TestOnPreToolCall:
    """_on_pre_tool_call — intercept writes to system paths."""

    def test_non_write_tool_returns_none(self):
        assert _on_pre_tool_call(tool_name="read_file", args={"path": "/etc/passwd"}) is None
        assert _on_pre_tool_call(tool_name="search_files", args={"pattern": "test"}) is None

    def test_missing_args_returns_none(self):
        assert _on_pre_tool_call(tool_name="write_file", args=None) is None
        assert _on_pre_tool_call(tool_name="write_file") is None
        assert _on_pre_tool_call() is None

    def test_user_path_returns_none(self):
        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/home/user/test.txt", "content": "data"},
        )
        assert result is None

    def test_unknown_path_blocked(self):
        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/custom/path/lib.so", "content": "data"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "[SBL]" in result["message"]
        assert "Unclassified" in result["message"]

    def test_terminal_non_write_command_returns_none(self, no_snapshot):
        """Non-write terminal commands (ls, cat) should not trigger SBL."""
        result = _on_pre_tool_call(
            tool_name="terminal",
            args={"command": "ls -la"},
        )
        assert result is None, (
            f"Read-only terminal commands should not trigger SBL, got: {result}"
        )

    def test_terminal_no_write_read(self, no_snapshot):
        """cat, head, tail, etc. should not trigger SBL."""
        for cmd in ("cat /etc/passwd", "head -5 /var/log/syslog", "tail -f /var/log/nginx/access.log"):
            result = _on_pre_tool_call(
                tool_name="terminal",
                args={"command": cmd},
            )
            assert result is None, f"Read-only command '{cmd}' should not trigger SBL"

    def test_system_path_no_snapshot_triggers_snapshot(self, monkeypatch, no_snapshot):
        """When snapshot hasn't been taken yet, SYSTEM path triggers snapshot."""
        with monkeypatch.context() as m:
            import plugins.sbl as _sbl_mod
            mock_take = MagicMock(return_value=ServiceMap())
            m.setattr(_sbl_mod, "_take_snapshot", mock_take)
            m.setattr(_sbl_mod, "_has_snapshot", lambda: False)
            result = _on_pre_tool_call(
                tool_name="write_file",
                args={"path": "/etc/nginx/nginx.conf", "content": "data"},
            )
            mock_take.assert_called_once()
            # No deps in empty service map, so result is None
            assert result is None

    def test_system_path_snapshot_fails_returns_none(self, monkeypatch, no_snapshot):
        """If snapshot fails, we don't block the write."""
        with monkeypatch.context() as m:
            import plugins.sbl as _sbl_mod
            m.setattr(_sbl_mod, "_has_snapshot", lambda: False)
            m.setattr(_sbl_mod, "_take_snapshot", MagicMock(side_effect=RuntimeError("no systemd")))
            result = _on_pre_tool_call(
                tool_name="write_file",
                args={"path": "/etc/nginx/nginx.conf", "content": "data"},
            )
            assert result is None  # best-effort: let the write through

    def test_system_path_with_deps(self, populated_service_map, monkeypatch):
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/etc/nginx/nginx.conf", "content": "bad config"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "[SBL]" in result["message"]
        assert "nginx" in result["message"]
        assert "Writing to /etc/nginx/nginx.conf affects running services" in result["message"]

    def test_system_path_no_deps(self, populated_service_map, monkeypatch):
        """SYSTEM path without known dependencies returns None (no warning)."""
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/etc/some-unknown-service/config.toml", "content": "data"},
        )
        assert result is None  # no deps known for this path

    def test_terminal_systemctl(self, populated_service_map, no_snapshot, monkeypatch):
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        result = _on_pre_tool_call(
            tool_name="terminal",
            args={"command": "systemctl restart nginx"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "nginx" in result["message"]

    def test_terminal_echo_redirect(self, populated_service_map, no_snapshot, monkeypatch):
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        result = _on_pre_tool_call(
            tool_name="terminal",
            args={"command": "echo 'test' >> /etc/hosts"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "networking" in result["message"]

    def test_patch_tool_system(self, populated_service_map, no_snapshot, monkeypatch):
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        result = _on_pre_tool_call(
            tool_name="patch",
            args={"path": "/etc/ssh/sshd_config"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "ssh" in result["message"]

    def test_empty_path_terminal_not_blocked(self, no_snapshot):
        """Empty path from terminal commands with no write pattern should not be blocked."""
        result = _on_pre_tool_call(
            tool_name="terminal",
            args={"command": "grep pattern /etc/somefile"},
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# 6. Transform Tool Result Hook (Learning)
# ═══════════════════════════════════════════════════════════════════════════

class TestOnTransformToolResult:
    """_on_transform_tool_result — learn new paths after writes."""

    def test_non_write_tool_returns_none(self):
        result = _on_transform_tool_result(
            tool_name="read_file",
            args={"path": "/etc/nginx/nginx.conf"},
            result='{"content": "data"}',
        )
        assert result is None

    def test_missing_args_returns_none(self):
        assert _on_transform_tool_result(tool_name="write_file") is None

    def test_error_result_returns_none(self):
        """If the tool itself errored, don't learn from it."""
        result = _on_transform_tool_result(
            tool_name="write_file",
            args={"path": "/etc/nginx/nginx.conf", "content": "data"},
            result={"error": "Permission denied"},
        )
        assert result is None

    def test_user_write_returns_none(self):
        result = _on_transform_tool_result(
            tool_name="write_file",
            args={"path": "/home/user/test.txt", "content": "data"},
            result='{"status": "ok"}',
        )
        assert result is None

    def test_system_write_learns(self):
        """System write should learn the path (add to change_log)."""
        import plugins.sbl as _sbl_mod

        result = _on_transform_tool_result(
            tool_name="write_file",
            args={"path": "/etc/nginx/nginx.conf", "content": "new config"},
            result='{"status": "ok"}',
        )
        assert result is None  # hook always returns None

        # Check change_log was updated
        entries = [c for c in _sbl_mod._change_log if "/etc/nginx/nginx.conf" in c.get("path", "")]
        assert len(entries) >= 1

    def test_system_write_patch_tool(self):
        import plugins.sbl as _sbl_mod

        result = _on_transform_tool_result(
            tool_name="patch",
            args={"path": "/etc/ssh/sshd_config"},
            result='{"status": "applied"}',
        )
        assert result is None

        entries = [c for c in _sbl_mod._change_log if "/etc/ssh/sshd_config" in c.get("path", "")]
        assert len(entries) >= 1

    def test_terminal_system_learns(self):
        import plugins.sbl as _sbl_mod

        result = _on_transform_tool_result(
            tool_name="terminal",
            args={"command": "echo 'test' >> /etc/hosts"},
            result='{"exit_code": 0}',
        )
        assert result is None

        entries = [c for c in _sbl_mod._change_log if "/etc/hosts" in c.get("path", "")]
        assert len(entries) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 7. Learning Mechanics
# ═══════════════════════════════════════════════════════════════════════════

class TestLearnChange:
    """_learn_change — internal learning from tool calls."""

    def test_non_write_tool_ignored(self):
        _learn_change("read_file", {"path": "/etc/nginx/nginx.conf"})
        assert not _change_log

    def test_user_path_ignored(self):
        _learn_change("write_file", {"path": "/home/user/test.txt", "content": "data"})
        assert not _change_log

    def test_system_path_new_dependency(self, populated_service_map):
        """New system path learns dependency from lookup, adds to file_owners."""
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        _learn_change("write_file", {"path": "/etc/nginx/nginx.conf", "content": "data"})
        assert len(_change_log) == 1
        assert _change_log[0]["path"] == "/etc/nginx/nginx.conf"

    def test_system_path_unknown_deps_adds_custom(self):
        """Path not in file_owners gets 'custom' as service."""
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = ServiceMap()
        _sbl_mod._snapshot_taken = True

        _learn_change("write_file", {"path": "/opt/custom-app/config.yml", "content": "data"})
        assert len(_change_log) == 1
        assert _change_log[0]["path"] == "/opt/custom-app/config.yml"
        # Should be added to file_owners with 'custom'
        assert "/opt/custom-app/config.yml" in _sbl_mod._service_map.file_owners

    def test_unknown_path_ignored(self):
        """Paths that classify as UNKNOWN are not learned."""
        _learn_change("write_file", {"path": "/weird/path/data", "content": "data"})
        assert not _change_log

    def test_terminal_echo_user_path_ignored(self):
        _learn_change("terminal", {"command": "echo 'hello' >> /tmp/test.txt"})
        assert not _change_log

    def test_learn_persistence(self, tmp_path, monkeypatch):
        """Learned changes persist to learned_deps.json (via mock snapshot dir)."""
        import plugins.sbl as _sbl_mod

        # Set up a temp snapshot dir directly
        snap_dir = tmp_path / "sbl-snapshot"
        snap_dir.mkdir()
        monkeypatch.setattr(_sbl_mod, "_SNAPSHOT_DIR", snap_dir)

        # Populate service map
        _sbl_mod._service_map = ServiceMap(
            services={"nginx": {"ports": ["80"], "configs": ["/etc/nginx/"], "type": "systemd"}},
            file_owners={"/etc/nginx/": ["nginx"]},
        )
        _sbl_mod._snapshot_taken = True

        learned_file = snap_dir / "learned_deps.json"
        assert not learned_file.exists()

        _learn_change("write_file", {"path": "/etc/nginx/nginx.conf", "content": "data"})

        assert learned_file.exists()
        data = json.loads(learned_file.read_text())
        assert "file_owners" in data
        assert "/etc/nginx/nginx.conf" in data["file_owners"]


# ═══════════════════════════════════════════════════════════════════════════
# 8. SBL Handler Commands
# ═══════════════════════════════════════════════════════════════════════════

class TestHandleSBL:
    """/sbl command handler."""

    def test_status_no_snapshot(self, no_snapshot):
        result = _handle_sbl_snapshot("status")
        assert "No snapshot" in result

    def test_status_default_no_snapshot(self, no_snapshot):
        result = _handle_sbl_snapshot("")
        assert "No snapshot" in result

    def test_status_with_snapshot(self, populated_service_map):
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        result = _handle_sbl_snapshot("status")
        assert "Status" in result
        assert "3 services" in result  # nginx, ssh, docker
        assert "6 configs" in result

    def test_snapshot_command(self, monkeypatch, no_snapshot):
        import plugins.sbl as _sbl_mod
        mock_take = MagicMock(return_value=ServiceMap(
            services={"nginx": {}},
            file_owners={"/etc/nginx/": ["nginx"]},
        ))
        monkeypatch.setattr(_sbl_mod, "_take_snapshot", mock_take)

        result = _handle_sbl_snapshot("snapshot")
        assert "Snapshot updated" in result
        assert "1 services" in result
        assert "1 config" in result
        mock_take.assert_called_once()

    def test_deps_no_snapshot(self, no_snapshot):
        result = _handle_sbl_snapshot("deps")
        assert "No snapshot" in result

    def test_deps_all(self, populated_service_map):
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        result = _handle_sbl_snapshot("deps")
        assert "Dependency Map" in result
        assert "/etc/nginx/" in result
        assert "/etc/hosts" in result

    def test_deps_filter(self, populated_service_map):
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        result = _handle_sbl_snapshot("deps /etc/hosts")
        assert "Dependencies for /etc/hosts" in result
        assert "networking" in result

    def test_deps_filter_no_match(self, populated_service_map):
        import plugins.sbl as _sbl_mod
        _sbl_mod._service_map = populated_service_map
        _sbl_mod._snapshot_taken = True

        result = _handle_sbl_snapshot("deps /nonexistent")
        assert "No known dependencies" in result

    def test_changes_empty(self):
        result = _handle_sbl_snapshot("changes")
        assert "No changes recorded" in result

    def test_changes_with_entries(self):
        import plugins.sbl as _sbl_mod
        _sbl_mod._change_log.append({"tool": "write_file", "path": "/etc/test.conf", "timestamp": "2026-01-01T00:00:00"})

        result = _handle_sbl_snapshot("changes")
        assert "Change Log" in result
        assert "1 entries" in result or "1 entry" in result
        assert "/etc/test.conf" in result

    def test_reset(self, monkeypatch, no_snapshot):
        import plugins.sbl as _sbl_mod
        _sbl_mod._snapshot_taken = True
        _sbl_mod._change_log.append({"tool": "write_file", "path": "/etc/test.conf", "timestamp": "x"})

        result = _handle_sbl_snapshot("reset")
        assert "reset" in result.lower() or "Reset" in result
        assert not _sbl_mod._snapshot_taken
        assert not _sbl_mod._change_log

    def test_deep_audit_import_error(self, monkeypatch, no_snapshot):
        """When deep_audit can't be imported, return helpful message."""
        import plugins.sbl as _sbl_mod
        monkeypatch.setattr(_sbl_mod, "_has_snapshot", lambda: False)

        # Simulate ImportError by patching the deep_audit import inside the handler
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def mock_import(name, *args, **kwargs):
            if "deep_audit" in name:
                raise ImportError("fd-find not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setitem(__builtins__ if isinstance(__builtins__, dict) else vars(__builtins__), "__import__", mock_import)

        result = _handle_sbl_snapshot("deep-audit")
        assert "unavailable" in result.lower() or "failed" in result.lower() or "Install" in result


# ═══════════════════════════════════════════════════════════════════════════
# 9. Snapshot Mechanism (tested via mocks — real snapshot requires root)
# ═══════════════════════════════════════════════════════════════════════════

class TestSnapshotMechanism:
    """_has_snapshot and _take_snapshot — basic behavior checks."""

    def test_has_snapshot_no_snapshot(self, no_snapshot):
        import plugins.sbl as _sbl_mod
        assert not _sbl_mod._has_snapshot()

    def test_has_snapshot_after_set(self):
        import plugins.sbl as _sbl_mod
        _sbl_mod._snapshot_taken = True
        assert _has_snapshot()

    def test_ensure_snapshot_dir_creates(self, tmp_path, monkeypatch):
        """_ensure_snapshot_dir creates the snap dir if possible."""
        import plugins.sbl as _sbl_mod
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(_sbl_mod, "_SNAPSHOT_DIR", None)
        monkeypatch.setattr(_sbl_mod, "_ensure_snapshot_dir", _ensure_snapshot_dir)

        # Force the real function by restoring it from the module
        with patch.object(Path, "home", return_value=fake_home):
            result = _sbl_mod._ensure_snapshot_dir()

        assert result is not None
        assert result.exists()
        assert "sbl-snapshot" in str(result)

    def test_snapshot_dir_cached(self, tmp_path):
        """Once set, _SNAPSHOT_DIR should be reused."""
        import plugins.sbl as _sbl_mod

        fake_home = tmp_path / "home2"
        fake_home.mkdir()
        _sbl_mod._SNAPSHOT_DIR = None

        with patch.object(Path, "home", return_value=fake_home):
            first = _ensure_snapshot_dir()
            second = _ensure_snapshot_dir()
            assert first == second


# ═══════════════════════════════════════════════════════════════════════════
# 10. Registration (smoke test)
# ═══════════════════════════════════════════════════════════════════════════

class TestRegistration:
    """register() — smoke test for hook registration."""

    def test_register_success(self):
        ctx = MagicMock()
        from plugins.sbl import register

        register(ctx)

        ctx.register_hook.assert_any_call("transform_tool_result", ANY)
        ctx.register_hook.assert_any_call("on_session_start", ANY)
        # pre_tool_call is now registered by governance plugin, not SBL
        ctx.register_command.assert_called_once_with(
            "sbl",
            handler=ANY,
            description=ANY,
        )

    def test_register_hook_failure_fallback(self):
        """If hooks fail, register_command still works (fallback)."""
        ctx = MagicMock()
        ctx.register_hook.side_effect = RuntimeError("hook failed")

        from plugins.sbl import register

        register(ctx)

        # Should still register the command despite hook failure
        assert ctx.register_command.call_count >= 1

    def test_register_complete_failure(self):
        """If everything fails, no crash."""
        ctx = MagicMock()
        ctx.register_hook.side_effect = RuntimeError("hook failed")
        ctx.register_command.side_effect = RuntimeError("cmd failed")

        from plugins.sbl import register

        # Should not raise
        register(ctx)
