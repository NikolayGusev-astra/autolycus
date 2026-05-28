# Deep Audit: 3 Approaches Comparison

## FMC (File Metadata Correlation)
- `fd` for config scanning → 2862 configs
- `/proc/*/comm` for processes → 159 processes
- `ss -tlnp` for ports → 28 ports
- `/etc/letsencrypt/live/` for cert cross-ref → 7 domains
- Active logs via `fd --changed-within 1h`
- Timing: ~1s

## Universal Config Probe
- `rg` on known config paths
- Extracts: ports, hosts, upstreams, file refs
- No per-service parser logic
- Timing: <0.1s

## Combo (FMC → Probe)
- FMC narrows to active services
- Probe only checks their configs
- Cross-service via shared paths (certs, logs, sockets)
- Result: nginx→xray (upstream), fail2ban→nginx (logs), all→certs

## Verified On
HQ (NL VPS): Ubuntu 22.04, Hermes Agent production.
