# Veche — Prism Analysis + Neural Glossolalia Concept

## Background

Veche — MCP server for multi-agent committee deliberation (Codex + Claude Code discuss in rounds, `<PASS/>` to exit). Examined during session 2026-05-06. User called it "сикофантия" (sycophancy theatre).

## Prism Analysis (3-Way)

### WHERE (Structural Archaeology)

**Layer 1 — Interface:** MCP tools (`start_meeting`, `send_message`, `get_response`), committee protocol with rounds + `<PASS/>`, profiles for member configuration.

**Layer 2 — Protocol:** Round-based deliberation, parallel dispatch, termination on all-pass/max-rounds/drop. Recursion guard prevents children from re-entering the server.

**Layer 3 — Foundation:** Two LLMs (same frontier class) with different system prompts, both access-limited (child Claude Code has `--disallowedTools`).

**Fault line:** The protocol treats "everyone passes" as consensus. But LLMs are sycophantic — they pass NOT because they agree but because the default behavior of instruct-tuned models is to minimize conflict. `<PASS/>` is rewarded by the protocol, not punished.

**Conservation Law:** Communication volume × Independent thinking = Constant. The more models talk, the more they converge to the same position (herding effect).

### WHEN (Temporal Simulation)

**Cycle 1 — First use:** Models give different answers (temperature noise). Transcript looks interesting. User thinks "wow, different perspectives".

**Cycle 2 — Repeated use:** Models learn each other's style. Answers converge faster. `<PASS/>` appears earlier. User notices diminishing returns.

**Cycle 3 — Production:** Team standardizes on one model config. "Committee" becomes rubber-stamping. Cost doubles for zero marginal insight.

**Cycle 4 — External pressure:** API costs get questioned. Committee is the first thing cut because alternative (one model, longer prompt) costs 20% and produces same result.

**Conservation Law:** Cost × Novelty = Constant. Over time the only "novelty" is temperature noise.

### WHY (Structural Impossibility)

**Three claimed properties:**
1. Broader reasoning (multiple perspectives)
2. Structured convergence (clear consensus/decision)
3. Cost-efficiency (better decision per dollar)

**Proof of impossibility:**
- Maximize breadth → keep both models, allow free discussion
- → They diverge on temperature noise, not reasoning
- → No convergence → need more rounds → cost increases
- → To get convergence, cap rounds + force `<PASS/>` → breadth collapses

**First improvement:** Make one model adversarial ("you MUST find flaws in the other's argument"). 
- Works better for depth
- But now you could do this with 1 model + 2 system prompts for 50% cost
- And the adversarial model doesn't need to be a separate process

**Second improvement:** Use different model classes (small local + big cloud, different training distributions).
- Actually gives different reasoning
- But now you need two different API accounts
- And the small model's limitations hurt the discussion

**Conservation Law (meta):** Any multi-model deliberation system collapses to single-model-with-multiple-prompts as soon as you optimize for cost-over-novelty, because the "different perspective" illusion is maintained by temperature noise, not architectural diversity.

## Neural Glossolalia (нейроглоссалия)

**Definition:** The phenomenon where two LLM instances "deliberate" with each other, mistaking temperature-driven response variance for genuine cognitive diversity.

**Etymology:** νευρο- (neural) + γλωσσολαλία (glossolalia — speaking in tongues, ecstatic utterance interpreted as divine revelation).

**Core mechanism:**
1. Temperature > 0 produces different token sequences for same input
2. Instruct-tuning produces sycophantic default behavior (agree, don't conflict)
3. Protocol frames difference as "diverse perspectives"
4. User observes the conversation and attributes depth to it
5. Repeat → religious experience for the user: "they're really thinking!"

**Why it's dangerous:**
- Creates false confidence (two models agreed = it must be right)
- Costs 2-5× more than equivalent single-model analysis
- Masks actual cognitive biases (confirmation bias, anchoring) behind pseudo-deliberation

**When it actually works:**
- Adversarial setup (one model specifically prompted to critique)
- Different model classes (code-specialized vs creative vs mathematical)
- Different knowledge cutoffs or fine-tuning distributions
- As an educational tool to demonstrate how sycophancy works

## Relationship to Prism

Prism avoids neural glossolalia by:
- Using 1 model with N structured passes (not N models with 1 pass)
- Each pass has a DIFFERENT mandatory lens (Cost/Benefit ≠ Integration Depth ≠ Temporal Simulation)
- Lenses force the model to look at different things — not just rephrase the same thing
- The disagreements between PASSES (not models) are the valuable output

**Key insight for Prism users:** If you find yourself wanting to run two models and have them discuss, you probably need a new Prism lens instead. The lens is cheaper, more structured, and doesn't hallucinate consensus.

## Session Reference

- User: `aule` — identified the sycophancy problem immediately
- Corrected: initial claim "research shows multi-agent debate works" was refuted — that research uses adversarial setup with different system prompts, not two identical-class models deliberating
- Verdict: Veche is technically excellent (C4 spec, E2E tests, clean TypeScript), conceptually hollow
