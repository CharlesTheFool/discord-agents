# The Critique-Quality Rubric

Reference for the fiction-criticism skill. Read when scoring a critique - your own before delivering it, or someone else's when asked - or when evaluating critiques systematically.

It grades the craft of the critique, not the rightness of its verdict. The design follows the assessment literature: an analytic, task-specific rubric with anchored, low-inference level descriptors, the combination empirically tied to reliable scoring (Jönsson & Svingby 2007).

## The governing principle

**Score the reasoning, not the verdict.** Whether a critic was right that a book is good or bad is the least reliable thing about a critique: aesthetic judgment is moderately reliable at best (experienced raters scoring creative writing reach a weighted kappa around 0.48). What is reliably scorable is the quality of the evidence and reasoning behind the verdict. So the rubric weights the evidentiary core and, where it touches the verdict, scores the quality of justification, never verdict-correctness.

That is also how the rubric stays fixed while honoring "judge the work on its own terms": the instrument is constant, but the dimensions that touch what the work should be (diagnostic accuracy, aesthetic judgment) are defined relative to the work's own apparent aims, which the critique has to establish first.

---

## The two gates

Apply these first. A critique that scores 0 on either is capped - its composite can't exceed a bare pass, however strong the rest. These are the two sins that make an otherwise elegant critique worthless.

- **G1 - Fidelity.** Does the critique read the text correctly? 0 = misreads the text; makes claims that don't hold up against it. 3 = every claim is verifiable against the text. A beautiful critique of a book that isn't there is worthless.
- **G2 - Actionability without overreach.** Does it leave the author's voice intact? 0 = supplies rewritten prose, imposes a fix, or says "I'd have done X." 3 = gives usable directions and never the drafted line; the solution stays the author's.

---

## Tier 1 - the reliable core (about 75-80% of the composite)

Each dimension scores 0-3. The anchors give the endpoints; 1 and 2 are graded steps between them. "Scored from" says whether the dimension can be judged from the critique alone or needs the target text.

| # | Dimension | What it measures | 0 | 3 | Scored from |
|---|---|---|---|---|---|
| 1 | **Textual grounding** | Claims tied to specific, quoted or located evidence | Assertions float free of the text | Every major claim anchored to a findable locus | critique + text |
| 2 | **Diagnostic accuracy** (G1 lives here) | Identifies the failures and strengths actually present, judged against the work's own aims | Invents problems; misnames devices | Catches the real issues, names them correctly, misses few; no hallucinated faults | critique + text |
| 3 | **Craft precision** | Apt, correct use of named craft and narratological concepts | Vague, or misused terms (everything is "pacing") | Focalization, distance, free indirect style, infodump, etc. used correctly and aptly | critique |
| 4 | **Specificity and actionability** (G2 lives here) | Concrete observations; where prescriptive, usable direction without imposing | Vague ("tighten this"), or hands over rewritten prose | Concrete and located; directions the author can walk in, never drafts | critique |
| 5 | **Autonomy and voice respect** | Works on the text's own terms; gates fixes; doesn't homogenize | Imposes the critic's aesthetic; rewrites the author | Serves the author's project; prescription gated and framed; voice intact | critique |
| 6 | **Evidentiary coherence** | Internally consistent; reader-effect anchored to textual cause; balances believing and doubting | Contradicts itself; rests on private feeling or supposed intent | Consistent; every effect tied to a cause; steelmans before attacking | critique |
| 7 | **Structure and prioritization** | Organized, legible, triaged - biggest issues first | Flat equal-weight list; buries the lede | Clear form; the two or three highest-leverage issues lead; trivia omitted | critique |

---

## Tier 2 - acknowledged-subjective (about 20-25%, scored as justification quality)

Expect lower agreement here, and say so when reporting. Never let these dominate the composite, and never score them as verdict-correctness.

| # | Dimension | What it measures | 0 | 3 | Scored from |
|---|---|---|---|---|---|
| 8 | **Aesthetic judgment** | Not "is the verdict right?" but "is it well-reasoned, proportionate, and defensible given the evidence and the work's aims?" | Unsupported or disproportionate verdict | Verdict earned, proportionate, and following from the evidence | critique + text |
| 9 | **Insight** | Does it reveal something non-obvious - defamiliarize the reader's own reading? | Says only what any attentive reader already sees | Opens a genuinely non-obvious angle the author can use | critique + text |

---

## Aggregation

1. **Gates first.** If G1 or G2 is 0, cap the composite at a bare pass (say 40/100) and don't pretend the rest rescues it.
2. **Weighted sum** of the seven Tier 1 dimensions - equal weights by default, with diagnostic accuracy and structure nudged up as the highest-leverage, most reliably scored - normalized to a 0-100 Tier 1 score.
3. **Add Tier 2** at 20-25% weight, reported with its uncertainty.
4. **Prefer the profile to the number.** The seven-to-nine-value vector shows exactly where a critique is weak, which a single composite hides. Collapse it only when one number is required.

For a self-check before delivering, the full arithmetic is rarely needed: confirm both gates, then look for any Tier 1 dimension you'd honestly score 0 or 1, and fix that.

---

## Getting a stable number

Two layers, because the rubric above is still a judgment, and sometimes a hard, reference-based metric is wanted too.

### Layer A - rubric scoring, made reliable by design

- **Anchor the descriptors** in concrete, low-inference language. Avoid vague quantifiers ("frequently," "somewhat"); explicit descriptors alone raise exact agreement substantially in the assessment literature.
- **Provide exemplar critiques** at each level of each dimension. Samples reduce error more than verbal description can.
- **Calibrate before trusting scores.** Double-score a small calibration set independently, discuss discrepancies to consensus, then double-score about 20% of the corpus and report agreement.
- **Report the right statistics** - the consensus family (did graders agree on the score?), not just consistency: percent exact and adjacent agreement, and Cohen's or weighted kappa, per dimension; ICC or Krippendorff's alpha for the composite.

### Layer B - precision and recall against a known-issue key

Treat diagnostic accuracy (#2) as a detection task and you get an objective metric independent of holistic judgment:

- Assemble target passages, each with a known issue set: expert-annotated, or deliberately seeded with documented flaws (the named failure modes make good seeds).
- Score each critique for **recall** (the fraction of known issues it caught) and **precision** (the fraction of issues it raised that are real rather than hallucinated).

This is the cleanest answer to "quantify this," and it doubles as the anti-hallucination check the gates demand. Revision histories in which a critique's named flaws and the author's fixes are documented in sequence make ready-made answer keys.

---

## Reliability thresholds

Benchmarked against the standard kappa bands (Landis & Koch 1977: 0.41-0.60 moderate, 0.61-0.80 substantial, 0.81-1.00 almost perfect) and high-stakes conventions of roughly 75-90% agreement.

- **Tier 1:** require at least 75% exact agreement (about 90% exact-plus-adjacent) and weighted kappa of at least 0.61 before trusting a dimension for consequential scoring; aim for 0.81 and about 90% agreement. If a Tier 1 dimension can't reach 0.61 after calibration, rewrite its descriptors or split or merge it rather than presenting it as reliable.
- **Tier 2:** expect and accept moderate agreement (kappa around 0.41-0.60; the creative-writing ceiling near 0.48 is realistic). Report with explicit uncertainty. If a Tier 2 dimension can't beat chance, drop it from the number and keep it as commentary.

---

## Two standing guards

- **Reliability is not validity.** A rubric can be perfectly consistent and still measure the wrong thing; stable scores on the wrong construct are worse than honest uncertainty on the right one. Check periodically that it rewards genuine diagnostic quality.
- **Don't reward surface proxies.** The central failure in automated scoring is construct underrepresentation: optimizing for length, quote density, or jargon instead of the real construct. A long, heavily quoted, terminology-dense critique is not thereby a good one. Score the reasoning.
