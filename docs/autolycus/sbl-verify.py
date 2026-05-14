"""SBL verification — run on target system where Autolycus is installed."""
import sys

# Autolycus repo root
sys.path.insert(0, "/opt/autolycus/repo")

from plugins.sbl import (
    _on_pre_tool_call, _classify_path, _take_snapshot,
    _has_snapshot, _lookup_dependencies,
)

errors = 0

def check(name, ok):
    global errors
    if not ok:
        print(f"  ✗ FAIL: {name}")
        errors += 1
    else:
        print(f"  ✓ {name}")

# 1. FHS Classification
print("=== 1. FHS Classification ===")
check("SYSTEM /etc/...", _classify_path("/etc/nginx/nginx.conf") == "SYSTEM")
check("USER /home/...", _classify_path("/home/user/test.txt") == "USER")
check("USER /tmp/...", _classify_path("/tmp/test") == "USER")
check("UNKNOWN /weird/...", _classify_path("/weird/lib.so") == "UNKNOWN")

# 2. Snapshot
print()
print("=== 2. Snapshot ===")
sm = _take_snapshot()
print(f"     Services: {len(sm.services)}")
print(f"     Ports: {len(sm.port_owners)}")
print(f"     Config deps: {len(sm.file_owners)}")
check("services_loaded", isinstance(sm.services, dict))

# 3. SYSTEM write blocked
print()
print("=== 3. SYSTEM write blocked ===")
r = _on_pre_tool_call(tool_name="write_file", args={"path": "/etc/hosts"})
if isinstance(r, dict):
    print(f"     Message: {r.get('message', '')[:100]}")
check("system_blocked", isinstance(r, dict) and "affects" in r.get("message", ""))

# 4. USER write passes
print()
print("=== 4. USER write passes ===")
r = _on_pre_tool_call(tool_name="write_file", args={"path": "/tmp/sbl-test.txt"})
check("user_write_passes", r is None)

# 5. UNKNOWN blocked
print()
print("=== 5. UNKNOWN blocked ===")
r = _on_pre_tool_call(tool_name="write_file", args={"path": "/weird/lib.so"})
msg = r.get("message", "") if isinstance(r, dict) else ""
check("unknown_blocked", "Unclassified" in msg)

# 6. systemctl passes (runtime cmd, not file write)
print()
print("=== 6. systemctl passes (runtime) ===")
r = _on_pre_tool_call(tool_name="terminal", args={"command": "systemctl restart nginx"})
check("systemctl_passes", r is None)

# 7. echo redirect detected
print()
print("=== 7. echo redirect detected ===")
r = _on_pre_tool_call(tool_name="terminal", args={"command": "echo 'x' >> /etc/hosts"})
msg = r.get("message", "") if isinstance(r, dict) else ""
check("echo_redirect_blocked", "affects" in msg)

# 8. read_file not blocked
print()
print("=== 8. read_file passes (non-write) ===")
r = _on_pre_tool_call(tool_name="read_file", args={"path": "/etc/passwd"})
check("read_file_passes", r is None)

print()
if errors == 0:
    print(f"=== ALL 8 TESTS PASSED ===")
else:
    print(f"=== {errors} FAILURES ===")
    sys.exit(1)
