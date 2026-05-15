# Tool-Level Guard Bypass — Case Study (May 2026)

## Проблема

Guard на уровне одного инструмента Hermes (write_file) bypassable агентом через любой другой инструмент (terminal, python, sed).

## Цепочка bypass'a

```
write_file защищён approval
    → агент переключается на: terminal(echo '...' | tee /etc/nginx/...)
    → echo и tee не в DANGEROUS_PATTERNS → прошло
    → добавляем echo, tee → агент переключается на: terminal(python3 -c "open('...','w').write('...')")
    → python3 -c не в DANGEROUS_PATTERNS → прошло
    → добавляем python3 -c → агент переключается на dd, install, compile C...
```

## Conservation law

`ToolGuard × AgentAdaptability = Constant`

Агент может использовать любой из 10+ способов записи в файл. Guard на одном — просто redirect на следующий.

## Единственное решение: guard на уровне ОС

Три уровня, где ВСЕ инструменты сходятся в одну точку:

| Уровень | Механизм | Не bypassable? | Сложность |
|---------|----------|----------------|-----------|
| **syscall** | fanotify (FAN_OPEN_PERM) | ✅ агент не может обойти write() | Средняя (демон на Python + fanotify) |
| **VFS** | chattr +i (immutable bit) | ✅ chattr требует root, меняется только явно | Низкая (одна команда) |
| **Permissions** | chmod go-rwx /etc/nginx/ | ✅ если агент не root | Низкая (уже настроено) |

## Когда tool-level guard всё же нужен

Tool-level guard (approval на write_file) имеет смысл как **первая линия** — ловит 40% сценариев без изменения ОС. Вторая линия — OS-level guard (fanotify, chattr). Третья — etckeeper (git для /etc, safety net после факта). Каждый следующий уровень ловит то что пропустил предыдущий, но ни один не полагается на «агент вспомнит».

## Ключевой инсайт

Проблема «агент не помнит про guard» решается не добавлением guard'а в контекст (вытесняется), а размещением guard'а на уровне, где agent'ское внимание не требуется — syscall/VFS.
