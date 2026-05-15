# Prism Self-Analysis: Agent's Own Responses

Use Prism-full on the agent's own answer before final delivery when:
- The answer makes claims about tool/skill capabilities (risk of over-selling)
- The answer contains a proposed workflow/pipeline
- The user is asking for a recommendation or comparison

## Pattern: Prism → Doc Verification → Corrected Delivery

1. **Write initial answer** — produce the response normally
2. **Apply Prism-full to the answer** — design 2-4 passes specific to this text:
   - *Scope & Completeness*: what did I promise vs what actually exists? Matrix per claim.
   - *Activation Energy*: how much setup does the user need? What's the real gap between "it exists" and "it works for them"?
   - *Inversion*: assume the answer is wrong and prove it. What alternatives are better? What problems does my suggestion create?
3. **Adversarial pass** — attack your own findings. What evidence would disprove each claim? What did all passes take for granted?
4. **Verify claims against docs** — check upstream repo, bundled skills, built-in tools. Patch incorrect absolutes.
5. **Rewrite the final answer** — incorporate corrections, remove over-claims, add nuance.

## Passes That Worked (from May 2026 session)

### Pass 1: Scope & Completeness Audit
For each promised capability in the answer: does it exist? What's the gap between what I said and what the tool actually does? Matrix: step | обещано | реально есть | gap.

### Pass 2: Activation Energy & Friction
Heat map: 🟢 работает из коробки / 🟡 надо настроить / 🔴 нужен фундамент или не существует.

### Pass 3: Inversion
Assume the recommendation is wrong. Prove it: what alternatives are cheaper/simpler? What does the agent add (overhead, complexity) vs solve?

### Adversarial Pass
For each finding: what evidence would disprove it? Did I overclaim (stated as structural when it's actually fixable)? Did I underclaim (missed implications)? What did ALL passes take for granted?

### Mandatory Sub-Pass: Fact Verification for Technical Claims

When self-analysis involves **specific empirical claims** (package names, library versions, API limits, performance numbers, prices), add this sub-pass BEFORE the adversarial pass:

**Protocol:**
1. List every specific claim: exact names (`minhash-lsh`), numbers (5MB, 100KB, $0.01), performance estimates (<100ms, 40%)
2. Verify each against a PRIMARY source:
   - Package names → `npm view`, `pip show`, `cargo search`
   - API limits → official docs, spec (Chrome.storage.local = 10MB MV3, not 5MB MV2)
   - Performance → actual benchmarks (datasketch has Hall of Fame), not gut feel
   - Pricing → current API pricing page, not historical
3. Classify each: ✅ CONFIRMED | ⚠️ NEEDS CORRECTION | ❌ REFUTED
4. Report corrections explicitly — don't silently fix, the adversarial pass needs to see what was wrong

**Common failure modes found in practice (May 2026):**
- npm package names guessed from memory: `minhash-lsh` does not exist (404), correct name is `bloom-filters` or `minhash`
- Storage limits from obsolete spec: Chrome.storage.local = 10MB in MV3, not 5MB (MV2)
- "Оценка по опыту" passed as fact: label estimates as expert opinion, not data

**Rule:** If the claim is specific enough to be *refutable*, it must be verified. If it can't be verified — mark it as "estimate" explicitly. No naked numbers.

## When to Trigger

- User asks "как Hermes Agent может помочь с X?"
- User asks "есть ли скилы для Y?"
- User asks "что ты умеешь / что может твой инструмент?"
- Any answer that makes capability claims without verifying against documentation

## Case Study: May 2026

**Scenario:** Developer asks if Hermes Agent can support a pipeline (epic → feature → proposal → plan → code).

**Initial answer:** Over-sold. Claimed Prism generates proposals (it analyzes, not generates), claimed SDD works for any project (needs context), claimed human gates have click-UI (they're CLI/chat).

**Prism revealed:** 3 of 7 pipeline steps had critical/high gaps. Adversarial pass found missing discovery phase and missing documentation step.

**Fix:** Verified claims against docs, rewrote answer with accurate framing: "Prism doesn't generate code — you write a spec, it analyzes it and gives structured recommendations. SDD works when you provide project context. No click-UI — all via chat/CLI."

**Lesson:** Prism self-analysis caught over-selling that the agent didn't see in the initial draft. The adversarial pass was particularly valuable — it revealed the "engineering pipeline vs product UX" framing mismatch that was the actual core issue.
