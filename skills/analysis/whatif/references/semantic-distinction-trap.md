# Semantic Distinction Trap — Premortem Finding Pattern

## The Pattern

An architectural critique claims a distinction that sounds real but is functionally meaningless. The critic says "X ≠ Y — X has property P that Y lacks" but in practice both X and Y behave identically.

**The test:** "If I delete this distinction, does the system's behavior change?" If no — the distinction is semantic.

## Case Study: Anchor ≠ Cache in Memory Architecture (May 2026)

**The claim (from Prism analysis):**
- Anchor layer in chat object = bad (two sources of truth, consistency drift)
- Cache in chat object = good (can be invalidated, no drift)

**The reality:** Both are a copy of system DB data stored in the chat object for fast access. The only difference is *invalidation policy* — but nothing prevents an "anchor" from being invalidated on session start. The semantic distinction ("anchor is authoritative, cache is ephemeral") doesn't hold in practice because:
- An anchor can be refreshed = functionally a cache
- A cache with TTL = functionally an anchor (persists until invalidated)
- Users don't care what it's called — they care about speed vs freshness

**What the Premortem revealed:** The Premortem (F8) found this distinction was semantic. The fix didn't change behavior — it just renamed the component. This demoted the finding from "structural certainty" to "preference."

## How to Detect in Your Own Premortems

When a failure reason claims a design flaw based on a distinction, ask:

1. Does this distinction survive a **behavioral test**? (Same inputs → different outputs?)
2. Does the critic's proposed fix **change component names only**, or actual data flow?
3. If you remove the distinction's terminology from the description — does the problem still exist?

**Early warning:** The finding includes words like "actually," "fundamentally," "by definition" followed by a binary classification (X ≠ Y). These are often semantic distinctions dressed as structural findings.

## How It Maps to Premortem Steps

In Step 3 (raw premortem generation), flag any failure reason that:
- Criticizes terminology over behavior
- Proposes renaming as a solution
- Cites a distinction that exists only in documentation, not in observable system behavior

These are weak failure reasons and should be either removed or explicitly downgraded in the synthesis.
