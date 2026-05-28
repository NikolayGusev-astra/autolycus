# Результаты динамического аудита на HQ (82.21.153.76)

## Найдено сервисов: 56

### Топ по портам

| Сервис | Порты | Конфиги (найдено) |
|--------|-------|-------------------|
| stalwart | 8080, 4190, 25, 465, 41479, 993, 995 | /opt/stalwart/ |
| nginx | 80, 8443, 443 | /etc/nginx/ |
| node | 3000, 36721, 3002 | — |
| ssh | 18888, 13000 | /etc/default/ssh, /etc/ssh/ |
| xray | 4433, 4443 | /etc/xray/, /usr/local/etc/xray/ |
| ollama | 11434 | — |
| postgres | 5432 | — |
| systemd-resolve | 53 | — |
| python3 | 8088, 9091 | /opt/xray-subscription/serve_sub.log |
| uvicorn | 8551 | — |
| python | 8642 | — |

### Системные сервисы (без портов)

chrony, dbus, fail2ban, family-vpn-bot, hermes-gateway, hermes-workspace, irqbalance, multipathd, pm2-root, polkit, rsyslog, secaudit, udisks2, upower, uuidd, vpn-subscription, и др.

### File owners (18 записей)

| Путь | Сервисы |
|------|---------|
| /etc/nginx/ | nginx |
| /etc/xray/ | xray |
| /usr/local/etc/xray/ | xray |
| /opt/stalwart/ | stalwart |
| /etc/fail2ban/ | fail2ban |
| /etc/ssh/ | ssh, sshd, ssh-tunnel-* |
| /etc/default/chrony | chrony |
| /etc/default/irqbalance | irqbalance |
| /etc/default/ssh | ssh |
| /opt/secaudit/.env.production | secaudit |
| /root/.hermes/.env | hermes-gateway |
| /opt/xray-subscription/serve_sub.log | python3 |
| /etc/hosts | networking |
| /etc/ssh/sshd_config | ssh |

### Аномалии/интересное

- `python3` на портах 8088, 9091 — это vpn-subscription server. Не systemd unit, найден через ss + /proc.
- `node` на 3000, 36721, 3002 — hermes-workspace + ещё что-то.
- `uvicorn` на 8551 — не systemd, найден через ss.
- `postgres` на 5432 — systemd unit, но в файле юнита не указан конфиг.
- `secaudit` — отдельный сервис с .env.production.
- `hermes-gateway` использует /root/.hermes/.env (найдено через systemctl cat EnvironmentFile).
