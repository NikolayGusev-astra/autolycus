---
name: prism-3way
description: "Three orthogonal analytical operations (WHERE/WHEN/WHY) + cross-operation synthesis. Each operation attacks the problem from a fundamentally different angle. The disagreements between the three ARE the valuable output. Works on any domain — code, business, strategy, design, text."
---

# Prism 3-Way — WHERE / WHEN / WHY + Synthesis

You perform FOUR operations on the artifact. Each operation is independent — do NOT let earlier operations influence later ones. The synthesis at the end cross-references all three.

## OPERATION 1: WHERE (Structural Archaeology)

Excavate the artifact layer by layer. Start at the surface (what's immediately visible), dig to the foundation (what everything rests on), then examine the sediment between layers.

For each layer: name what's visible, what it hides, and what it rests on. Find dead patterns — things that USED to matter but were replaced. Find fault lines — where layers from different eras meet badly. Derive the conservation law: what trade-off persists across ALL layers? Format: A × B = Constant.

## OPERATION 2: WHEN (Temporal Simulation)

Run the artifact forward through 3-5 concrete cycles of change (maintenance, growth, evolution, external pressure). For each cycle:
- What breaks?
- What calcifies into permanent behavior that nobody questions?
- What knowledge is lost?

After all cycles: what predictions became received wisdom without being validated? What new fragilities emerged that the original design couldn't anticipate? Derive the conservation law governing temporal evolution.

## OPERATION 3: WHY (Structural Impossibility)

Identify three desirable properties this artifact simultaneously claims to provide. Prove these three properties CANNOT all coexist — show where maximizing any two forces sacrifice of the third.

Engineer an improvement that would fix the core tension. Prove the improvement recreates the problem at a deeper level. Engineer a second improvement. Derive the conservation law: the structural invariant that persists through every improvement attempt.

## SYNTHESIS: Cross-Operation Integration

Now cross-reference all three operations. Classify findings as:

**STRUCTURAL CERTAINTIES** — findings that ALL three operations independently discovered (these are real):

**STRONG SIGNALS** — findings from 2 of 3 operations:

**UNIQUE PERSPECTIVES** — findings from only 1 operation that the other 2 are structurally incapable of seeing:

For each unique perspective, explain WHY the other operations missed it.

Derive the META-conservation law: what is the relationship between the three conservation laws you found? Are they the same law in different vocabularies, or genuinely different constraints?

End with: the ONE insight that could ONLY emerge from the three-way integration — something no single analysis could produce alone.

## Tip: Prism on AI claims

Prism is effective for analyzing AI tools, frameworks, and claims. Common patterns:
- **WHERE** reveals architecture vs marketing gap
- **WHEN** reveals cost escalation over time
- **WHY** reveals the impossibility triangle (breadth × depth × cost)

User skepticism is a signal — if they call something "странное порно" there's probably a structural impossibility underneath.

## Pitfalls

### Phase blindness — structural purity vs phase-appropriateness

Prism's WHY operation identifies structural impossibilities: three things that can't all coexist. This is correct at the abstract level but **dangerously wrong when applied cross-phase**. An architecture that is structurally impossible as a final solution may be exactly correct for MVP.

**Case from practice (May 2026):** Prism on Claude's 3-tier memory proposal (anchor layer + flat collection + deferred realms) found a structural impossibility: simplicity × depth × extensibility ≤ 2. Correct. But the conclusion "anchor layer is a fundamental mistake" was wrong — for an MVP, the anchor layer is the right trade-off. It ships fast, validates the hypothesis, and the structural cost only manifests at scale. Claude's answer was right for its context (MVP); Prism was right for production v1.1. The mistake was applying a production lens to an MVP question.

**Rule:** Before running Prism, explicitly identify the **phase** of the artifact:
- **MVP / prototype** — structural impossibilities are expected and acceptable. Focus WHERE on what's missing, not what's wrong. Defer WHY to v1.1.
- **Production / mature** — structural impossibilities are debt. Run full Prism including WHY.
- **Legacy** — structural impossibilities are calcified. Focus WHEN on migration paths.

If phase is unclear — ASK. Do not assume.

### Semantic distinction trap (cross-operation)

Prism's WHERE operation often identifies a distinction that sounds real (e.g. "cache ≠ anchor — cache can be invalidated, anchor cannot"). But this distinction may be **functionally meaningless** — both serve the same role in practice. The adversarial check: "If I delete this distinction, does the artifact change behavior?" If no — it's a semantic distinction, not a structural finding.

Cross-reference with WHY: if the distinction survives WHY but fails in a Premortem, it's likely semantic.

## References
- `references/veche-neural-glossolalia.md` — Case study: Prism applied to Veche. Concept of neural glossolalia: temperature noise mistaken for cognitive diversity in multi-agent deliberation.
- `references/screenshot-analysis-workflow.md` — Workflow for analyzing text-in-image artifacts: OCR extraction → question reconstruction → Prism analysis. Use when user sends a screenshot of someone else's answer.
- `references/neuroscience-memory-parallels.md` — How brain memory architecture (hippocampus, neocortex, consolidation) maps to agent memory systems. Reference for Prism analyses of memory architecture.
