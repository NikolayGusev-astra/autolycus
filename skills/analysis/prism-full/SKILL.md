---
name: prism-full
description: "Full Prism: multi-pass structural analysis with adversarial falsification. Design custom analytical passes per artifact, execute with chaining, then falsify your own findings — producing structural discoveries no single-pass method can reach."
---

# Prism — Multi-Pass Structural Analysis

## Philosophy

Prism is a **philosophical method** for structural analysis. Not a checklist — a way of thinking.

**Core claim:** Every artifact deserves custom analytical lenses, designed specifically for its structure, domain, and purpose. Generic checklists produce generic findings. Custom passes produce discoveries — conservation laws, impossibility triangles, retracted assumptions — that survive falsification.

**Method: Design → Execute → Falsify → Synthesize**

You perform THREE phases. All are mandatory.

---

## Pre-Condition: Artifact Inventory

Before ANY analysis, verify what you're actually analyzing. Analysis of documentation alone is analysis of architecture dreams — not reality.

**For code artifacts** — read the source files. Not README, not architecture docs. Verify claims against actual implementation: does the claimed feature exist? does the hook get called? is the config active? Document discrepancies between what's described and what exists.

**For non-code artifacts** (strategy, text, business) — verify completeness and authenticity. Check for missing sections, edits, selective presentation that would change the analysis. Ask: "Is this artifact what it claims to be?"

**Conservation principle:** You cannot analyze what you haven't verified exists.

---

## Epistemological Pre-Check: Know What You Don't Know

Before designing any passes — check YOUR position.

**Three questions before Phase 1:**

1. **What am I assuming about this artifact?** — Model limits? How it will be consumed (agent instructions vs human reading)? Domain familiarity? Every assumption you don't articulate is a blind spot.

2. **Can I state what would DISPROVE my first intuition?** — You WILL have a strong intuition immediately after reading the artifact. This intuition is often spectacularly wrong. If you cannot articulate what evidence would falsify it — you haven't understood your own bias.

3. **What do I NOT know?** — Before claiming anything, list the things you cannot verify from the available data. These are your epistemic gaps. Phase 1 passes should be designed to CLOSE gaps, not to confirm what you already think.

**Socratic principle:** Confidence is inversely proportional to falsification effort. The more certain a finding feels, the harder you must try to disprove it before publishing.

---

## Phase 1: Design Analytical Passes

You are a pipeline architect. Design 2–4 analytical passes specific to THIS artifact. Not generic — custom-tailored.

### Principles of Good Lenses

Each lens must force you to CONSTRUCT something, then diagnose what the construction reveals. Study these examples:

**9.5/10** — "Identify every explicit choice. Name the alternative each invisibly rejects. Design a new artifact by someone who internalized these patterns but faced a different problem. Trace which transferred patterns create silent problems. Name the pedagogy law."

**9/10** — "Extract every empirical claim about timing, causality, resources, or behavior. Assume each is false. Trace the corruption. Build three alternatives inverting one claim each. Predict which false claim causes the slowest, most invisible failure."

**8.5/10** — "Capability inventory pass. Inventory what the underlying platform already provides. For EACH component: (a) native equivalent? (b) if yes, what justified building custom? (c) what breaks if I delete this and use native?"

### Pass Design Rules
- **Pass 1** analyzes the raw artifact
- Each subsequent pass receives the artifact + ALL previous analysis
- Each pass: 75–200 words of compressed analytical instructions
- Each pass forces **construction → diagnosis**, never passive observation

**Output:** your pipeline under "## Generated Pipeline" — show each pass with its role.

---

## Phase 2: Execute + Mandatory Falsification

Execute every pass in order. For each:
1. State which pass you are executing
2. Execute against the artifact (pass 2+ against artifact + all previous analysis)
3. Output complete analysis

### MANDATORY FINAL PASS: Falsification

After ALL designed passes, execute one UNDESIGNED pass — never planned in Phase 1:

Attack your own findings. For each conservation law, structural claim, or bug reported:
- What evidence would DISPROVE it?
- Did you **overclaim**? (stated as structural when it's actually fixable)
- Did you **underclaim**? (missed something your analysis implies)
- What did ALL your passes take for granted that might be wrong?
- What would Socrates say? — If you can't state what you don't know, you don't know what you're claiming.
- If the artifact was pre-compressed/filtered: your passes analyzed survivors, not the original. What claims collapse if unseen tokens restored?

Overclaim → **RETRACT**. Underclaim → **ADD**.

**Epistemological principle:** Confidence without falsification is not knowledge — it's narrative. A finding that survives every designed pass but was never challenged is not a finding, it's an assertion.

**Adversarial pass is the only honest pass** — especially when analyzing your own work (see Self-Analysis extension below).

### Pre-Publication Falsification

The adversarial pass comes AFTER execution. But the falsification mindset must start BEFORE Phase 1.

Before designing ANY pass, ask:
- «What would my analysis conclude, and what evidence would disprove that conclusion?»
- «What am I assuming about this artifact that I haven't verified?»
- «If my central claim turned out to be wrong, what alternative explanation fits the same data?»

This is **pre-publication falsification** — falsify your hypothesis before you've invested analysis into it. The Phase 2 adversarial catches analytic overclaims. Pre-publication falsification catches framing errors — wrong questions that no amount of correct analysis can salvage.

---

## Phase 3: Synthesis

Produce the final reconciled output.

### Structural Conservation Law
The structural property that survives falsification. Format: A × B = Constant.

### Retracted Claims
What the falsification disproved (if any).

### Findings Table
| Finding | Location | What Breaks | Severity | Nature |
|---------|----------|-------------|----------|--------|

Severity: 🔴 critical / 🟡 significant / 🟢 minor. Nature: Fixable / Structural / Investment.

### Deepest Finding
What became visible ONLY because falsification challenged the analytical passes.

### Recommendations
3–5 concrete items, classified as:
- **Fixable** — can fix now
- **Investment** — worth doing, needs time/resources
- **Structural** — not fixable, must be designed around

---

## Post-Analysis: Save Results

1. Structured results auto-save to `~/wiki/raw/auto-findings/` (via `findings_to_wiki` provider) or manually to `~/wiki/concepts/`
2. For wiki pages: use `research/llm-wiki` skill for ingest with frontmatter, cross-links, verification status

---

## Method Extensions

These extend Prism to specific contexts without changing the core method.

### Chaining with WhatIf Premortem
ADR → Prism → Premortem pipeline. Prism finds structural problems; Premortem adds narrative failure scenarios. Complementary — use both for high-stakes decisions.

### Post-hoc Prism (Retrospective)
After completing work, analyze the execution:
- **Plan vs Reality** — time estimate vs actual, hidden complexity factor
- **Decision Quality** — what was the best alternative?
- **Risk Materialization** — which risks materialized, which didn't?
- **Missing Risks** — what went wrong that wasn't in any risk register?

### Self-Review: Prism on Own Code
Apply Prism to your own PR before submission. Reads diff → designs 2–3 passes → delegates each to subagent (with full tool access — `toolsets=[]` breaks code analysis) → adversarial pass → fix → update PR.

See `references/prism-pr-self-review.md`. Proven (May 2026, PR #22093): 7 bugs found that author-review missed.

### Self-Analysis: Prism on Agent's Own Responses
Apply Prism to your own answer before delivery — catches overselling, inflated promises, framing mismatch. Especially when making claims about tool/skill capabilities or proposing workflows.

For self-analysis: **invert effort** — spend 80% on adversarial, 20% on design. Your own designed passes will tend to confirm what you wrote. Only the adversarial pass sees the actual problems.

**Critical: Apply Prism to the METHOD itself.** This session (May 2026) proved that Prism applied to prism-full revealed:
- Overclaim about "scaling problem" (actually flat priority structure)
- Misdiagnosis of artifact consumption medium (human wiki ≠ agent instructions)
- Codebase Reality Check violated for the analyst's OWN context assumptions

The skill that analyzes everything must also analyze ITSELF. If your analysis feels too clean — apply Prism to it. The deepest finding will be what your own passes were designed to miss.

See `references/prism-self-analysis.md`.

---

## Pitfalls

When your analysis feels too clean (no contradictions found, every pass converges), or when you want to check against known failure patterns:

→ Load `references/prism-pitfalls.md`

Contains ~20+ documented traps organized by phase: pre-analysis (prior-knowledge blindness, platform-blind), execution (premature synthesis, rabbit-hole), post-analysis (inertia, good-enough paralysis), delegation (subagent timeout), comparison (target ambiguity), and structural impossibilities (Knowledge×Memory×Scope ≤ 2).

**Load pitfalls when:**
- The user asks for pitfall-checking
- Your analysis found zero contradictions or uncertainties
- You're applying Prism to a domain you haven't analyzed before
- **Best timing:** after Phase 2 (adversarial pass), BEFORE Phase 3 (synthesis). Checking pitfalls after synthesis means redoing work.

---

## References

### Method Extensions (keep with core)
- `references/prism-pr-self-review.md` — full self-review workflow with passes and case study
- `references/prism-self-analysis.md` — self-analysis pattern for agent responses
- `references/prism-plugin-design-case.md` — code-prism vs service-prism comparison
- `references/dual-system-comparison.md` — specialized pipeline for comparing two technologies
- `references/code-audit-fix-pipeline.md` — audit-before-fix methodology
- `references/audit-before-build.md` — verify existing hooks/ABCs before designing
- `references/codebase-inventory-checklist.md` — detailed codebase inventory steps

### Application References (domain-specific Prism variants)
- `references/document-session-plugin-architecture.md` — Prism applied to document session plugin design
- `references/test-plan-analysis.md` — Prism on test plans before writing tests
- `references/test-quality-analysis.md` — Prism on existing tests for green-washing detection
- `references/mcp-tool-analysis-checkpoints.md` — Prism applied to MCP tool analysis
- `references/external-article-workflow.md` — Prism for analyzing external articles
- `references/research-taxonomy-prism.md` — Prism for research taxonomy analysis

### Reference Files (detailed case studies, loaded on demand)
- `references/prism-pitfalls.md` — agent failure pattern catalog (THIS is the extracted trap collection)
- All other existing references remain available on demand