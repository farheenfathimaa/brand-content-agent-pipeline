"""Markdown report writer for the eval harness."""

from __future__ import annotations

from pathlib import Path


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _num(value: float | None, places: int = 3) -> str:
    return "—" if value is None else f"{value:.{places}f}"


def render_report(result: dict) -> str:
    meta = result["meta"]
    metrics = result["metrics"]
    rows = result["results"]
    lines: list[str] = []
    lines.append("# Eval report — Brand Content Agent Pipeline")
    lines.append("")
    lines.append(f"- **Run:** `{meta['run_id']}`")
    lines.append(f"- **Generated:** {meta['generated_at']}")
    lines.append(f"- **Mode:** `{meta['mode']}`")
    lines.append(f"- **Commit:** `{meta.get('commit') or 'unknown'}`")
    lines.append(f"- **Duration:** {meta['duration_seconds']:.2f} s")
    lines.append(f"- **Golden briefs:** {meta['brief_count']}")
    lines.append(
        f"- **Policy:** max revision loops = {meta['max_revision_loops']}, "
        f"back-translation threshold = {meta['translation_similarity_threshold']:.2f}"
    )
    lines.append("")
    lines.append("## Prompt versions")
    lines.append("")
    lines.append("| Prompt | Version |")
    lines.append("| --- | --- |")
    for name, version in sorted(meta["prompt_versions"].items()):
        lines.append(f"| `{name}` | `{version}` |")
    lines.append("")
    lines.append("## Headline metrics")
    lines.append("")
    lines.append("| Metric | Value | Definition |")
    lines.append("| --- | --- | --- |")
    lines.append(
        f"| Guardrail pass rate | {_pct(metrics['guardrail_pass_rate'])} | "
        "Share of golden briefs whose **final** draft has zero error-severity violations. |"
    )
    lines.append(
        f"| Unsupported-claim rate | {_pct(metrics['unsupported_claim_rate'])} | "
        "Share of golden briefs whose **final** draft has at least one unsupported "
        "numeric, product, or promotional claim. |"
    )
    lines.append(
        f"| Initial unsupported-claim rate (diagnostic) | "
        f"{_pct(metrics['initial_unsupported_claim_rate'])} | "
        "Same check applied to the first draft **before** critic revisions. |"
    )
    lines.append(
        f"| Brand-voice rubric score | {_num(metrics['brand_voice_rubric_score'], 2)} / 5 | "
        "Mean LLM-as-judge score across four rubric dimensions. |"
    )
    lines.append(
        f"| Back-translation similarity | {_num(metrics['back_translation_similarity'])} | "
        "Mean lexical similarity (`difflib` ratio) between English source and English "
        "back-translation, all languages. |"
    )
    lines.append(
        f"| Minimum back-translation similarity | "
        f"{_num(metrics['min_back_translation_similarity'])} | "
        "Lowest single translation similarity in the run. |"
    )
    lines.append(
        f"| Translation pass rate | {_pct(metrics['translation_pass_rate'])} | "
        "Share of translations at or above the configured threshold. |"
    )
    lines.append(
        f"| Briefs requiring revision | {metrics['briefs_revised']} / {meta['brief_count']} | "
        "Briefs that entered the critic revision loop at least once. |"
    )
    lines.append(
        f"| Human overrides | {metrics['overrides']} | "
        "Briefs approved with `force=true` after unresolved guardrail errors. |"
    )
    lines.append(
        f"| Webhook deliveries recorded | {metrics['webhook_deliveries']} | "
        "Signed webhook delivery attempts (skipped when no URL is configured). |"
    )
    lines.append("")
    lines.append("## Per-brief results")
    lines.append("")
    lines.append(
        "| Brief | Vertical | Revisions | Initial guardrails | Final guardrails | "
        "Unsupported initial | Unsupported final | Judge | Min similarity |"
    )
    lines.append("| --- | --- | ---: | --- | --- | --- | --- | ---: | ---: |")
    for item in rows:
        lines.append(
            f"| `{item['id']}` | {item['vertical']} | {item['revisions']} | "
            f"{'pass' if item['initial_passed'] else 'fail'} | "
            f"{'pass' if item['final_passed'] else 'fail'} | "
            f"{'yes' if item['initial_unsupported'] else 'no'} | "
            f"{'yes' if item['final_unsupported'] else 'no'} | "
            f"{_num(item['judge_score'], 2)} | {_num(item['min_similarity'])} |"
        )
    lines.append("")
    lines.append("## Interpretation and limits")
    lines.append("")
    if meta["mode"] == "mock":
        lines.append(
            "- **Mock mode uses deterministic fake providers.** These numbers exercise "
            "the harness, state machine, guardrails, revision loop, and translation "
            "check. They are **not** evidence of live LLM writing quality."
        )
        lines.append(
            "- The mock judge is a transparent heuristic over the guardrail report, not "
            "a live LLM judgment. The live judge prompt is versioned under "
            "`app/prompts/judge/` and only runs with `APP_MODE=live`."
        )
        lines.append(
            "- The mock translator uses a small dictionary and a language marker. "
            "Similarity is high by construction. It verifies wiring, not translation "
            "quality."
        )
    else:
        lines.append(
            "- Live mode results depend on the selected provider, model versions, and "
            "sampling temperature. Re-run after any prompt or model change."
        )
    lines.append(
        "- Back-translation similarity is **lexical**, not semantic. It catches gross "
        "omissions and drift, not subtle mistranslation."
    )
    lines.append(
        "- Groundedness checks digits and a fixed list of promotional claim patterns. "
        "Spelled-out numbers and novel claim phrasings can evade it."
    )
    lines.append("")
    lines.append(
        "This report is generated by `python -m app.evals.runner` (or `make eval`). "
        "Every value above comes from the run that produced this file."
    )
    lines.append("")
    return "\n".join(lines)


def write_report(result: dict, output_dir: str | Path) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    report = path / "eval_report.md"
    report.write_text(render_report(result), encoding="utf-8")
    return report
