# ADR-006: Automated Crash Recovery via SBL

## Status
Proposed | Date: 2026-05-27

## Context
After a datacenter crash (Hostkey, 2026-05-27), manual recovery took ~3 minutes. Process: user asked agent → agent ran SBL audit → found 2 failed services (xray port conflict, stale trading-bot) → applied fixes → verified all 34 services.

**Problem**: requires explicit user command. On a real outage, every second counts. Recovery should be automatic.

**Key insight**: SBL already knows the system topology (`service_map.json`). Crash recovery is just `diff(current, last_known_good)`.

## Decision: Check → Trigger → Action → Report Pipeline

Four-phase pipeline triggered automatically at boot:

1. **CHECK**: Full SBL audit (systemctl + ss + /proc/PID/fd) → `audit_snapshot.json`
2. **TRIGGER**: Diff against `service_map.json` → anomaly list
3. **ACTION**: Apply fixes by safety category (🟢 auto / 🟡 auto+notify / 🔴 human)
4. **REPORT**: Telegram message with full status

## Anomaly Detection Rules

| Rule | Type | Example |
|------|------|---------|
| Service in map but not running | `service_down` | xray.service failed |
| Port occupied by wrong PID | `port_conflict` | nginx on 8443 instead of xray |
| Unit running but not in map | `stale_unit` | trading-bot 2 years unused |
| Dependency not running | `missing_dep` | redis down → api-gateway too |
| PID missing expected fds | `broken_fd` | xray has no listener on expected port |
| Config changed since last audit | `config_drift` | xray/config.json modified |

## Action Safety Categories

🟢 **Safe** (auto): service_down, port_conflict, stale_unit
🟡 **Advisory** (auto + notify): missing_dep, config_drift
🔴 **Escalate** (human only): 3+ consecutive crashes, unknown conflict

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| False positive (race at boot) | startup_grace_period=15s before audit |
| Cascade restart breaks another | restart_order + health-check after each fix |
| Lost service_map | Keep 3 latest versions |
| Infinite recovery loop | max_retries=3 then escalate |
| Double launch (cron + systemd) | flock exclusive lock |

## Key Principle

SBL already knows what a "healthy" system looks like. Crash recovery is simply diff(current_state, last_known_good_state), applied intelligently.
