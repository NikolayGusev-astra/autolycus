# Test Quality Analysis — 3-Pass Prism Pipeline

Когда Prism применяется к unit-тестам, стандартные analytical passes недостаточны.
Тесты — особый артефакт: они могут быть «зелёными» не потому что проверяют контракт,
а потому что подогнаны под код (LLM green-washing, см. проблему «почему CoT не является
отражением мыслительного процесса»).

Используй этот 3-pass pipeline + adversarial pass для анализа тестов.

## Pass 1: Structural Integrity & Self-Consistency

Проверь каждый тест:
- **Проверяет ли он output функции или implementation detail?** — assert на return value
  контракта (blocked/not blocked, trust_score >= threshold), не на внутренние переменные.
- **Есть ли assert вообще?** — скрипты без assert (print-only) проходят при любом выводе.
- **Устойчив ли к ребалансировке?** — `>=`/`<`/`pytest.approx` vs точные константы.
  Точные константы хороши только если они документируют порог (threshold = 0.3, trust = 0.0).
- **No-op тесты** — блок `if condition: pass` или `assert True` — не проверяют ничего.

**Симптомы green-washing:**
- assert True
- except: pass
- if orig: pass (no-op graceful degradation test)
- Тест называется одно, а assert проверяет противоположное (misleading name)

## Pass 2: Negative Space Analysis

Что НЕ тестируется? Для каждого тестового файла:

1. **Edge cases**: None, empty string, очень длинные строки (10K+), невалидные типы
2. **Race conditions**: глобальное состояние при параллельных вызовах (если есть разделяемое состояние)
3. **Encoding bypass**: base64, HTML entities, quoted-printable — обходят pattern matching
4. **Payload splitting**: injection разделённый на несколько сообщений
5. **All variants**: все source_type, channel, tool_name форматы
6. **Integration**: E2E цепочка модулей (supply_chain → sanitize → mcp → sbl)

**Чеклист для быстрой оценки:**
- [ ] Есть ли тесты на None/empty input?
- [ ] Есть ли тесты на invalid/unexpected source_type/channel?
- [ ] Есть ли тесты на very long input (10K+)?
- [ ] Есть ли тесты на unicode/homoglyph obfuscation?
- [ ] Есть ли мisnamed тесты (docstring/name ≠ assert)?
- [ ] Есть ли no-op тесты (pass/if True)?
- [ ] Есть ли тесты threshold boundary?

## Pass 3: Green-Washing Detector

Для каждого assert:
- Может ли он быть True при полностью сломанной функции?
  - `assert len(result) > 0` — сломанная функция вернёт пустой список, assert упадёт ✓
  - `assert result is not None` — сломанная функция вернёт None, assert упадёт ✓
  - `assert "ALL TESTS PASSED" in output` — скрипт print'ает фразу независимо от логики ✗
- Писался ли тест одновременно с кодом одной рукой?
  - Если все test cases покрывают только happy path — подозрительно
  - Если все error paths testing тестируют через simulates/a mock, а не через real error — подозрительно
- Есть ли assert на внутренние детали (implementation coupling)?
  - `assert result._internal_metric == 42` — сломается при рефакторинге
  - `assert result.trust_score > 0.3` — контракт, не сломается

## Adversarial Pass: Attack Your Findings

1. **Overclaim check**: Каждое структурное утверждение — что его опровергает?
2. **Underclaim check**: Какие тесты твой анализ не заметил, но они есть?
3. **Architectural constraint**: Некоторые тесты невозможно написать иначе — E2E требует
   запущенного сервера, graceful degradation test требует удаления модуля (нельзя в том же процессе).
   Учитывай это — не все gaps это green-washing.
4. **Real green-washing vs acceptable tradeoff**: print-only prototype test для ручной верификации
   на victim Docker — acceptable если коммит явно помечен как prototype. Но он НЕ должен
   учитываться в test coverage metrics.
5. **Самый опасный пропущенный баг**: threshold boundary — если threshold=0.0 (always block или
   never block), тесты всё равно пройдут. Добавь специальный тест для границы.

## Applied: Case Study (May 2026 — SBL + MCP + Supply Chain + Sanitize)

На 4 тестовых файлах (91 тест) pipeline нашёл:

| Находка | Severity | Fix |
|---------|----------|-----|
| SBL prototype: 0 assert'ов | 🔴 HIGH | Добавлены assert'ы во все 10 тестов |
| MCP graceful degradation: no-op test | 🟡 MEDIUM | Заменён на AST-проверку except-путей |
| MCP test name: misleading (says not-blocked, asserts blocked) | 🟢 LOW | Переименован |
| MCP: нет threshold boundary test | 🟡 MEDIUM | Добавлен test_trust_threshold_boundary |
| MCP: нет deep nested tool names | 🟢 LOW | Добавлен test_deep_nested_tool_name |
| Supply Chain: нет invalid source_type | 🟢 LOW | Добавлен test_invalid_source_type_fallback |

**Conservation law:** `ContractTests × SolipsismRisk = Constant`.
Чем больше тестов проверяют контракт (blocked/not blocked, trust >= threshold),
тем меньше риск что они «зелёные» при сломанном коде.
Чем больше тестов проверяют implementation detail (точные константы, внутренние
структуры), тем выше риск green-washing при рефакторинге.

## References

- `sbl-system-boundary-layer/SKILL.md` — применимо к тестам SBL
- `hermes-agent-security-hardening/SKILL.md` — security-тесты используют похожие паттерны
