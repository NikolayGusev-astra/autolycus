# Screenshot Analysis Workflow: OCR → Reconstruction → Prism

## When to use

When the user sends a screenshot/photo of someone else's answer, article, or UI, and asks for your analysis. The image might be low-quality, partial, or contain only an answer without the original question.

## Workflow

### Step 1: OCR extraction

```bash
# Basic OCR (try rus+eng for bilingual text)
tesseract /path/to/image.jpg - -l rus+eng --psm 6

# If quality is poor, preprocess with Python PIL:
python3 -c "
from PIL import Image, ImageEnhance
img = Image.open('/path/to/image.jpg')
# Scale up (3-5x)
img = img.resize((W*4, H*4), Image.LANCZOS)
# Boost contrast
enhancer = ImageEnhance.Contrast(img)
img = enhancer.enhance(2.0)
img.save('/tmp/enhanced.png')
"
# Then OCR the enhanced version
tesseract /tmp/enhanced.png - -l rus+eng --psm 6

# For white text on dark background: invert first
python3 -c "
from PIL import Image, ImageOps
img = Image.open('/path/to/image.jpg')
img = ImageOps.invert(img)
img = img.resize((W*5, H*5), Image.LANCZOS)
img.save('/tmp/inverted.png')
"
```

**PSM modes to try:**
- `--psm 6`: Assume uniform block of text (best for code/output)
- `--psm 3`: Fully automatic (default)
- `--psm 4`: Single column of text
- `--psm 11`: Sparse text (good for noisy images)
- `--psm 12`: Sparse text with OSD

### Step 2: Reconstruct the question

From the extracted answer fragments, work backward:
1. What problem does this answer solve?
2. What terms/entities does it refer to that aren't explained? (those were defined in the question)
3. What trade-offs does it acknowledge? (the question probably asked about those)
4. What does it defer? (the question probably asked about timeline/scoping)

**Example:** Answer says "agent_memory as anchor layer + flat collection + defer realms to v1.1"
→ Question was: "How to architect agent memory for MVP? Where to store what, and what to defer?"

### Step 3: Apply Prism to the problem itself

Once the question is reconstructed, analyze it with Prism 3-Way:
- **WHERE:** What's the structural layering of the proposed solution? Where are the fault lines?
- **WHEN:** How does the solution age? What breaks in cycles 2-4?
- **WHY:** What three properties are claimed but can't coexist?

### Step 4: Formulate your independent answer

Present your own architecture/analysis, not a critique of the original answer. The goal is to answer the reconstructed question, not to rebut the screenshot.

## Pitfalls

1. **Don't assume the screenshot is complete.** It might be cropped, a partial scroll, or the middle of a longer answer. The reconstruction is a hypothesis, not a fact.
2. **OCR noise will corrupt key terms.** `agent_memory` → `арепЕ_шешогу`. Use context to reverse-corrupt: technical terms in English survive OCR better if the image has black text on white.
3. **Resist the urge to critique the original answer's style.** You're analyzing the architecture, not reviewing the answer's presentation.
4. **If OCR fails completely** (image too small, handwriting, diagram), say so explicitly and ask for text transcription.
