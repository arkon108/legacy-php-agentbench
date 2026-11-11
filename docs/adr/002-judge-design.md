# ADR 002: Judge design — different model family, round limit

**Status:** accepted  
**Date:** 2026-11-04  

## Context

Config B adds a judge that reviews the worker's patch before it is accepted.
The judge must decide whether the diff is a plausible fix for the reported bug,
given the diff itself and the test output.

Three design choices need to be made:

1. **Which model judges?** The same model as the worker, a larger model, or a
   model from a different family?
2. **How many review rounds?** The judge can reject a patch and return feedback.
   How many times before the system gives up or takes the last patch?
3. **What happens after the round limit?** Discard the patch (strict) or submit
   it anyway (lenient)?

## Decision

1. **Different model family.** The worker is qwen3.7-plus (Qwen family); the
   judge is glm-5.3 (GLM family). Using a different family reduces the risk of
   correlated errors — if the worker misunderstands the bug, a model trained on
   different data is more likely to catch it.

2. **At most 2 rejection rounds.** After the worker's initial patch, the judge
   can reject twice. Each rejection returns the judge's feedback to the worker,
   which produces a new patch. After 2 rejections (3 total patches), the system
   moves to step 3.

3. **Lenient: take the last patch.** If the judge rejects twice, the system
   submits the worker's most recent patch anyway. The alternative — discarding
   the patch — wastes the worker's effort entirely. Since the worker sometimes
   produces correct fixes that the judge rejects (the judge is not infallible),
   a lenient policy preserves those cases.

## Rationale

**Different family over same model or larger model.** Using the same model as
both worker and judge risks the judge agreeing with the worker's mistakes —
a form of confirmation bias. Using a strictly larger model (e.g. qwen3.8-max
as judge) would add cost and still share the Qwen family's biases. A
cross-family judge is the cheapest way to get an independent signal.

**2 rounds, not 1 or 3+.** One round gives no opportunity for the worker to
incorporate feedback. Three or more rounds risk the worker oscillating between
approaches (fix → revert → different fix → revert) while burning tokens. Two
rounds balance feedback opportunity against token cost. In practice, most
resolved runs succeed on the first patch or after one round of feedback.

**Lenient over strict.** The benchmark data shows that 3/27 Config B failures
involved judge timeouts — the judge (glm-5.3) timed out after 300s on all 3
runs of dw-004, causing wrong fixes to be accepted without review. A strict
policy would have discarded those patches, but it would also discard correct
patches that the judge mistakenly rejects. Since judge accuracy is imperfect,
lenient submission preserves more signal for evaluation.

## Consequences

- The judge adds latency: each round requires a full API call to glm-5.3, with
  a 300s timeout. Runs that go to 2 rejection rounds can take significantly
  longer than Config A runs.

- Judge timeouts are a failure mode unique to Config B. When the judge times
  out, the patch is accepted without review — equivalent to no judge at all.
  This happened on 3/27 failures (all dw-004).

- The lenient policy means Config B's resolved count includes patches the judge
  never approved. This is a feature, not a bug: it lets us compare the judge's
  value-add against the baseline of just taking the worker's output.

- Cross-family judging means the judge may have different strengths and
  weaknesses than the worker. The judge cannot run tools or read files — it
  sees only the diff, test output, and the original bug report. This limits
  what the judge can catch to issues visible in the diff itself.
