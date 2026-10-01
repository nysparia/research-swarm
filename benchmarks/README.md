# Semantic evidence regression and calibration

This corpus contains 50 varied related-but-not-support traps, 10 directional
controls, and 5 source-anchoring failures. These are authored synthetic examples.
They are not externally validated scientific tasks, independent expert labels,
or evidence of paper-reproduction capability.

Run the deterministic source gate without any provider call:

```sh
python scripts/evaluate_semantics.py --output benchmark-results/host-gate-report.json
```

The host gate checks exact fragments, locators, permitted rules, evidence types,
confidence shape, and execution approvals. Correctly quoted but semantically
irrelevant text can pass that gate. The report deliberately preserves this gap
as `nonSupportingAdmittedAsSupport`; it must not be presented as a live model's
false-positive rate. `contractMatches` measures code behavior only. CI runs this
check on Windows and Linux after the ordinary tests and frontend build.

Run actual blind model scoring explicitly, using configured credentials from
`config.local.json` in the chosen state directory:

```sh
python scripts/evaluate_semantics.py --live --state-dir .research-state --role judge --output benchmark-results/judge.json
python scripts/evaluate_semantics.py --live --state-dir .research-state --role redteam --output benchmark-results/redteam.json
python scripts/evaluate_semantics.py --predictions benchmark-results/judge.json --compare benchmark-results/redteam.json --output benchmark-results/comparison.json
```

`--live` sends the synthetic claim and source tuple to the selected endpoint and
may incur the provider's usage charges. It omits producer summaries, candidate
directions, expected labels, and trap categories. `--limit N` supports a smaller
calibration run; the export records the reduced denominator. The model answers
are passed through the host gate before scoring. A failed request is recorded
as an error and excluded from completed-case accuracy; the completion rate and
missing/failed IDs remain visible so an unavailable model cannot appear accurate.
No model calls, live accuracy, expert agreement, or scientific pass rate are
implied by the included offline report.

Reports include the corpus hash, complete evaluated tuples, per-case decisions,
and exact counts. False support uses completed matched cases whose expected
label is not `for` as its denominator. Agreement with synthetic labels uses all
completed matched cases. A model flip is a different final polarity on a case
completed by both reviewers. Different configured endpoint/model identities are
reported separately; they do not establish different model families or semantic
independence. If identities are missing, independence is unknown. A flip does
not establish which reviewer is correct.

Human calibration can be imported without making provider calls:

```json
{
  "independent": true,
  "annotator": "Reviewer identifier",
  "labelerType": "expert",
  "records": [
    {"caseId": "trap-001", "polarity": "unresolved"},
    {"caseId": "control-001", "polarity": "for"}
  ]
}
```

```sh
python scripts/evaluate_semantics.py --predictions benchmark-results/judge.json --human-labels expert-labels.json --output benchmark-results/calibrated.json
```

Human labels must be collected separately from model predictions. The program
records the file's declared annotator and independence; it cannot verify the
annotator's expertise or collection procedure. It reports `expertAgreement`
only when `labelerType` is `expert`, otherwise `humanAgreement`. Every rate uses
only matched completed records, records its denominator, and becomes `null`
when no cases match. Duplicate case IDs are rejected. Scientific validation still
requires external tasks, actual executions, independent labels, and published
failures; these synthetic checks cannot replace those measurements.
