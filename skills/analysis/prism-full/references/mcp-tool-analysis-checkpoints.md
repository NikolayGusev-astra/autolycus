# MCP / Agent Tool Analysis Checkpoints

When analyzing any LLM-based tool or MCP server (Veche, Chorus, Quorum, etc.), README describes ideal behavior. Code reveals actual behavior. These checkpoints catch the critical differences.

## 1. System Prompt Injection

**Check:** How is the system prompt delivered to the model?

- **One-shot** (at session start only): Model remembers from conversation history. As session grows, system prompt drifts to the beginning of context.
- **Every turn** (reinjected): Model receives fresh instructions each round. More robust.
- **As part of prompt**: System prompt mixed with user message. Harder to audit.

**Veche finding:** `--append-system-prompt` on first turn only. Subsequent turns use `--resume` with no reinjection. `composeSystemPrompt()` returns `null` when `providerRef !== null`.

## 2. Default vs Custom Profiles

**Check:** What system prompt + role do participants get by default?

- **Identical defaults** — all participants are "peer" / "Independent committee member"
- **Different defaults** — built-in adversarial roles
- **Customizable** — can user override? How? (config file, MCP args, env vars?)

**Veche finding:** Default = `{name: "peer", description: "Independent committee member.", weight: 1}` for ALL model participants. Custom profiles require external `$VECHE_HOME/config.json`.

## 3. Context Filtering

**Check:** What does each participant see of other participants' messages?

- **Full transcript** — model sees everything every round
- **Delta since last round** — model sees only new messages since its last turn
- **Other-only** — model does NOT see its own previous responses
- **Last-N** — sliding window of most recent messages

**Veche finding:** `buildPrefixForParticipant()` filters: `if (m.author === self) continue; if (m.round < lastRound) continue;` — model sees ONLY other participants' messages since own last round. Own messages are preserved by provider-side `--resume`.

## 4. Termination Conditions (Perverse Incentives)

**Check:** How does the discussion end? Is there incentive to NOT stop?

- **Explicit stop** — someone says "stop"
- **Consensus threshold** — N% agree
- **PASS/opt-out** — participants can decline to respond
- **All-pass** — termination when all say PASS in same round

**Veche finding:** Termination on `all-passed` (everyone says `<PASS/>` in the same round). This creates incentive to KEEP TALKING because PASS is a step toward ending the discussion. LLMs trained to be helpful → will generate filler rather than trigger termination.

## 5. Temperature Control

**Check:** Is temperature configurable per participant? Is it different from default?

- Same temperature → same response distribution → same "opinion"
- Different temperature → different noise → illusion of disagreement

**Veche finding:** No temperature parameter in participant config or MCP interface. Temperature is whatever the provider defaults to (typically 0.7 for both).

## 6. Research Baseline

**Check:** Does the protocol match setups that actually work in research?

- **Adversarial** (Du et al. 2023) — participants explicitly instructed to find errors. Works.
- **Deliberative** (free-form discussion) — consensus-seeking. Does NOT work.
- **Veche:** Deliberative by default. Can be made adversarial via custom profiles, but defaults are not.

**Research reference:** arXiv:2509.23055 — sycophancy causes multi-agent debate to underperform single-agent baselines.

## Quick Audit Template

```python
checkpoints = {
    "system_prompt_reinjection": False,
    "default_roles_different": False,
    "own_messages_filtered": True,
    "perverse_incentive": True,
    "temperature_configurable": False,
    "adversarial_default": False,
}
# More than 2 True = high risk of neuroglossalia
```
