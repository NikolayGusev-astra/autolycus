# OpenSSH PerSourcePenalties — Code Autopsy (2026-05-07)

Источник ошибки в этой сессии: я ответил на вопрос про PerSourcePenalties, опираясь на статью на Хабре + 20-летний опыт работы с SSH. Не проверил исходники. Результат: ложное утверждение «whitelist не существует».

## Факты из исходников (OpenSSH 10.2p1, V_10_2)

### Whitelist — существует
**Файл:** srclimit.c, функция `srclimit_penalty_check_allow()`, строка 267-275:
```c
if (penalty_exempt != NULL) {
    if (addr_match_list(addr_s, penalty_exempt) == 1) {
        return 0; /* exempt */
```
**Конфиг:** `PerSourcePenaltyExemptList` в sshd_config.5 — CIDR, wildcard, список через запятую.
**Пример:**
```
PerSourcePenaltyExemptList 10.0.0.0/8,172.16.0.0/12
```
Вызов `addr_match_list()` — та же функция что используется в `Match Address`.

### Уровень логирования — INFO, не VERBOSE
**Файл:** srclimit.c, `srclimit_penalise()`, строка 450:
```c
logit_f("%s: activating %s penalty of %lld seconds for %s",
```
`logit` = LOG_INFO. Пишется на дефолтном уровне. Достаточно `journalctl -u sshd | grep penalty`.

Только deferred-штрафы (ниже порога min) используют `do_log2_f` с уровнем verbose (строка 429-433).

### Кумулятивный механизм (продление срока)
**Файл:** srclimit.c, строка 444-447:
```c
existing->expiry += penalty_secs;
if (existing->expiry - now > penalty_cfg.penalty_max)
    existing->expiry = now + penalty_cfg.penalty_max;
```
При повторном нарушении срок истечения продлевается. Кап — max (default 600s/10min).

### overflow:permissive — вытеснение старых записей
**Файл:** srclimit.c, функция `srclimit_early_expire_penalties_from_tree()`, строка 318-320:
```c
/* Delete the soonest-to-expire penalties. */
while (*npenaltiesp > max_sources) {
    p = RB_MIN(penalties_by_expiry, by_expiry);
```
Вытесняет штрафы с наименьшим временем оставшейся жизни.

### invaliduser — есть, не упомянут в статье
В мане и srclimit.c `SRCLIMIT_PENALTY_INVALIDUSER`, default 5s.
Статья на Хабре его не разбирает — пробел.

### Параметры по умолчанию (из sshd -T):
```
persourcepenalties crash:90 authfail:5 invaliduser:5 noauth:1 grace-exceeded:10 
refuseconnection:10 max:600 min:15 max-sources4:65536 max-sources6:65536 
overflow:permissive overflow6:permissive
```

## Причина ошибки
1. Статья на Хабре не упомянула `PerSourcePenaltyExemptList`
2. Я не проверил исходники — «я и так знаю OpenSSH»
3. Сделал категоричный вывод, который опровергается кодом за 2 минуты чтения

## Применимость
- Ansible control-нода: решается `PerSourcePenaltyExemptList` со своей подсетью
- CGNAT: не решается (IP-based, whitelist не поможет)
- Не-SSH сервисы: fail2ban всё ещё нужен
- Selective unban: нельзя без restart sshd (единственное оставшееся преимущество fail2ban)
