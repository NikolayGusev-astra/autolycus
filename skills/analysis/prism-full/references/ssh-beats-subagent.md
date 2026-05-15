# Self-Execution Over Subagent — Hardware Recon Pattern

## Проблема

При анализе hardware-level задач (хардверный рекон, сетевое сканирование, поднятие WiFi/BT) возникает
соблазн делегировать subagent'у. Premortem выявляет F4 (fake success) как топ-причину провала.

**Почему subagent не сработает:**
- У subagent'а нет доступа к удалённой машине (SSH, физический доступ)
- Subagent будет «исследовать» код/документацию вместо реального железа
- Subagent «подтвердит» что задача выполнена, хотя он ничего не делал
- Даже с инструментом `terminal` — subagent не знает куда SSH-иться

## Решение

Для hardware/system-level задач — **всегда SSH напрямую** из основного агента.
Никакой делегации. Premortem служит детектором: если топ-причина провала = 
«subagent наврёт что сделал» — не делегируй.

## Паттерн из практики (2026-05-05)

**Задача:** Хардверный рекон kozanout (Astra Linux, Lenovo ideapad 330-15IGM):
- lspci/lsusb/rfkill
- Поднятие WiFi (ath10k module reload)
- Поднятие Bluetooth (bluez install + systemctl start)
- Сканирование WiFi/BT сетей

**План:** Делегировать subagent'у на козаноут через Hermes Agent.

**Premortem:** F1 (Hermes мёртв), F4 (fake success), F5 (nmap нет), F8 (WiFi убьёт SSH).

**Prism adversarial pass:** «Зачем делегировать? У тебя есть SSH и shell.
Subagent добавит latency и overhead без пользы.»

**Execution напрямую:** SSH + shell. Весь рекон за 3 минуты. WiFi/BT подняты.

## Когда применять

- Хардверный рекон (lspci, lsusb, dmidecode)
- Сетевые сканы (nmap, ip, iw)
- Установка/настройка сервисов (apt, systemctl)
- Любая задача, требующая root-доступа на удалённой машине
- Любая задача, где «сделай и проверь» — единственный способ узнать результат

## Когда делегировать ВСЁ ЖЕ можно

- Subagent с доступом к той же машине через terminal
- Subagent с конкретными инструкциями и ограниченными инструментами
- Если задача — чисто когнитивная: анализ, синтез, сравнение
- Если subagent уже работает на целевой машине (local execution)
