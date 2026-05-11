---
name: ton-development
description: >-
  Разработка смарт-контрактов и dApps на TON Blockchain через Acton
  CLI. Создание, сборка, тестирование, деплой, верификация, генерация
  TypeScript wrappers и Telegram Mini Apps.
category: blockchain
author: Autolycus (gen-ii.ru)
version: 1.0.0
---

# TON Development — Acton Toolchain

Acton — официальная тулчейн от TON Blockchain (Rust) для разработки смарт-контрактов на языке Tolk.

**Слоган Acton:** *Built for humans. Perfect for AI.*

## Установка Acton

```bash
# Быстрая установка
curl -LsSf https://github.com/ton-blockchain/acton/releases/latest/download/acton-installer.sh | sh

# Проверка
acton --version

# Установка Agent Skills от Acton (дополнительно)
npx skills add -g https://github.com/ton-blockchain/acton-contracts/tree/skills/skills/
```

## Быстрый старт

```bash
# 1. Создать проект из шаблона
acton new my_contract --template counter
cd my_contract

# 2. Собрать
acton build

# 3. Протестировать (на Tolk, 50x быстрее JS sandbox)
acton test
acton test --ui      # визуальный просмотр трасс

# 4. Создать и funded кошелёк для тестнета
acton wallet new --name deployer --local --airdrop --version v5r1

# 5. Деплой
acton script scripts/deploy.tolk --net testnet

# 6. TypeScript wrapper для React-фронтенда
acton wrapper
```

## Команды Acton

### Управление проектом
| Команда | Описание |
|---------|----------|
| `acton new <name> --template <tpl>` | Новый проект (counter, empty, jetton, nft) |
| `acton init` | Добавить Acton в существующий проект |
| `acton build` | Скомпилировать контракты |
| `acton check` | Линтер |
| `acton fmt` | Форматтер |
| `acton fmt --check` | Проверка форматирования (CI) |

### Тестирование
| Команда | Описание |
|---------|----------|
| `acton test` | Запустить все тесты |
| `acton test --ui` | Тесты с UI (трассы, покрытие, gas) |
| `acton test --coverage` | С отчётом о покрытии |
| `acton test --mutate` | Mutation testing |
| `acton test --fuzz` | Fuzz testing |

### Кошельки и сеть
| Команда | Описание |
|---------|----------|
| `acton wallet new --local --airdrop` | Кошелёк с тестовыми монетами |
| `acton wallet list` | Список кошельков |
| `acton wallet balance` | Баланс |
| `acton rpc --method getAccount` | Запрос состояния аккаунта |
| `acton retrace <tx-hash>` | Повторить транзакцию локально |

### Деплой и верификация
| Команда | Описание |
|---------|----------|
| `acton script <file.tolk> --net testnet` | Запустить скрипт деплоя |
| `acton verify <address>` | Верифицировать контракт |
| `acton run <script-name>` | Запустить именованный скрипт из Acton.toml |

### Инструменты
| Команда | Описание |
|---------|----------|
| `acton wrapper` | TypeScript/Tolk wrapper из ABI |
| `acton compile <file.tolk>` | Компиляция одного файла |
| `acton disasm <file>` | Дизассемблировать TVM bytecode |
| `acton doc` | Справочник по инструкциям TVM |
| `acton doctor` | Диагностика окружения |
| `acton up` | Обновление Acton |
| `acton hooks` | Управление Git hooks |
| `acton completions` | Shell автодополнение |

## Структура проекта

```
my_project/
├── Acton.toml            # Манифест проекта
├── .acton/               # Кеш стандартной библиотеки
├── contracts/
│   ├── main.tolk         # Смарт-контракт
│   └── utils.tolk        # Вспомогательные функции
├── scripts/
│   ├── deploy.tolk       # Скрипт деплоя
│   └── interact.tolk     # Скрипт взаимодействия
├── tests/
│   └── main.tolk         # Тесты на Tolk
└── wrappers/
    └── Main.ts           # TypeScript wrapper (auto-generated)
```

## Telegram Mini Apps на TON

```bash
# 1. Создать контракт
acton new my_miniapp --template empty

# 2. Написать контракт, собрать, протестировать
acton build && acton test

# 3. Сгенерировать TypeScript wrapper
acton wrapper

# 4. Фронтенд: Vite + React + TON Connect
npm create ton-dapp@latest frontend

# 5. Зарегистрировать Mini App у @BotFather
# Команда: /newapp
```

## Архитектура

```
┌─────────────────────────────────────────────────────┐
│                   Telegram                          │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │ Mini App UI │  │   Bot        │  │  Wallet    │ │
│  │ (React)     │  │   (@bot)     │  │  (встроен) │ │
│  └──────┬──────┘  └──────┬───────┘  └─────┬──────┘ │
└─────────┼────────────────┼────────────────┼─────────┘
          │                │                │
          ▼                ▼                ▼
┌─────────────────────────────────────────────────────┐
│                   TON Blockchain                     │
│  ┌────────────────────────────────────────────────┐ │
│  │          Smart Contract (Tolk)                 │ │
│  │  • Storage: данные пользователей               │ │
│  │  • Get-methods: чтение (бесплатно)             │ │
│  │  • External messages: запись (плата за gas)    │ │
│  └────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

**Без сервера.** Вся логика — в смарт-контракте. Фронтенд — статика, хостится бесплатно.

## Практические кейсы

### 1. Приём платежей без эквайринга
Смарт-контракт-кошелёк принимает TON/токены. 0% комиссии, без банка, без Eвросети.

### 2. Токены доступа / подписки
Контракт автоматически разблокирует доступ на N дней после оплаты. Всё автоматически.

### 3. Прозрачные голосования
Результаты на блокчейне — нельзя подделать, нельзя отменить.

### 4. NFT / цифровые активы
Билеты, купоны, сертификаты — стандарт TON NFT.

### 5. Telegram Mini Apps без бэкенда
Замена Django/FastAPI на смарт-контракт. Не нужна VPS, не нужен админ.

### 6. Enterprise «блокчейн-решения»
Продаётся дорого. С Acton — делается за 5 минут.

## Полезные ссылки

- **Документация Acton:** https://ton-blockchain.github.io/acton/docs/welcome
- **Tolk язык:** https://ton-blockchain.github.io/acton/docs/tolk/overview
- **Туториал (полный dApp):** https://ton-blockchain.github.io/acton/docs/tutorial/overview
- **GitHub Acton:** https://github.com/ton-blockchain/acton
- **TON документация:** https://docs.ton.org
- **TON Dev Faucet:** https://t.me/tondevlive_bot
- **Telegram Mini Apps:** https://core.telegram.org/bots/webapps
