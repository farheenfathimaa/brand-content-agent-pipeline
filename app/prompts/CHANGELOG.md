# Prompt changelog

All prompts are versioned YAML files under `app/prompts/`. A released version is
never edited in place. Add a new file, register it in `registry.yaml`, and record
the change here.

| Date | Prompt | Version | Change |
| --- | --- | --- | --- |
| 2026-10-27 | `draft` | v1 | Initial brand-safe drafting prompt. |
| 2026-10-27 | `critic` | v1 | Initial guardrail-driven revision prompt. |
| 2026-10-28 | `translation` | v1 | Initial translation prompt. |
| 2026-10-28 | `back_translation` | v1 | Initial literal back-translation prompt. |
| 2026-10-29 | `judge` | v1 | Initial four-dimension brand-voice rubric. |
| 2026-11-01 | `draft` | v2 | Strict fact-sheet grounding, verbatim disclaimers, sentence-length limit. Active by default. |

## Policy

- `registry.yaml` is the source of truth for the active version.
- `PROMPT_DRAFT_VERSION`, `PROMPT_CRITIC_VERSION`, `PROMPT_TRANSLATION_VERSION`,
  and `PROMPT_JUDGE_VERSION` can pin a version per run.
- Eval reports record the active versions so a score is always attributable to a
  specific prompt set.
- The mock LLM does not read prompt text. Prompt changes only affect live mode;
  the mock still exercises the surrounding harness.
