"""Eval harness: run all golden briefs end-to-end and write a markdown report."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.config import Settings
from app.container import build_container
from app.enums import Language, Vertical
from app.evals.report import write_report
from app.schemas import ApprovalRequest, BriefCreate, PublishRequest

GOLDEN_BRIEFS_PATH = Path(__file__).parent / "golden_briefs.yaml"


class GoldenBrief(BaseModel):
    id: str
    title: str
    vertical: Vertical
    fact_sheet_id: str
    languages: list[Language] = Field(default_factory=list)
    brief: str
    stress_test: bool = False


def load_golden_briefs(path: str | Path | None = None) -> list[GoldenBrief]:
    source = Path(path) if path else GOLDEN_BRIEFS_PATH
    with source.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    entries = raw.get("briefs")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"No briefs found in {source}")
    briefs = [GoldenBrief.model_validate(entry) for entry in entries]
    if len({brief.id for brief in briefs}) != len(briefs):
        raise ValueError("Golden brief ids must be unique.")
    return briefs


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def run_eval(
    *,
    briefs_path: str | Path | None = None,
    output_dir: str | Path = "reports",
) -> dict:
    briefs = load_golden_briefs(briefs_path)
    started = time.perf_counter()
    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    run_id = f"eval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"

    with tempfile.TemporaryDirectory(prefix="bcap-eval-") as temp_dir:
        settings = Settings(
            _env_file=None,
            app_mode="mock",
            database_path=str(Path(temp_dir) / "eval.db"),
            webhook_url="",
            webhook_secret="",
            task_tracker="mock",
            design_provider="local",
            design_output_dir=str(Path(temp_dir) / "designs"),
            generate_design_assets=False,
        )
        container = build_container(settings)
        service = container.service

        rows: list[dict] = []
        errors: list[str] = []
        for golden in briefs:
            row = {
                "id": golden.id,
                "brief_id": "",
                "title": golden.title,
                "vertical": golden.vertical.value,
                "revisions": 0,
                "initial_passed": False,
                "final_passed": False,
                "initial_unsupported": False,
                "final_unsupported": False,
                "judge_score": None,
                "min_similarity": None,
                "mean_similarity": None,
                "translation_count": 0,
                "override": False,
                "state": "",
                "error": "",
            }
            try:
                payload = BriefCreate(
                    title=golden.title,
                    vertical=golden.vertical,
                    brief=golden.brief,
                    fact_sheet_id=golden.fact_sheet_id,
                    languages=golden.languages,
                    metadata={"stress_test": golden.stress_test},
                )
                prepared = service.create_brief(payload)
                row["brief_id"] = prepared.id
                initial = prepared.initial_guardrail
                final = prepared.guardrail
                row.update(
                    {
                        "revisions": prepared.revision_count,
                        "initial_passed": bool(initial and initial.passed),
                        "final_passed": bool(final and final.passed),
                        "initial_unsupported": bool(initial and initial.unsupported_claims),
                        "final_unsupported": bool(final and final.unsupported_claims),
                        "state": prepared.state.value,
                    }
                )
                rubric = [
                    {"id": item.id, "description": item.description}
                    for item in container.guardrails.pack_for(golden.vertical).brand_voice.rubric
                ]
                judge = container.llm.judge(
                    text=f"{prepared.headline}. {prepared.body}",
                    fact_sheet=prepared.fact_sheet,
                    rubric=rubric,
                    report=final,
                )
                row["judge_score"] = judge.score

                needs_override = bool(final and not final.passed)
                row["override"] = needs_override
                staged = service.approve(
                    prepared.id,
                    ApprovalRequest(
                        approver="eval-harness",
                        notes="Automated golden-brief eval run.",
                        force=needs_override,
                        override_reason=(
                            "Eval harness records unresolved pre-approval guardrail errors."
                            if needs_override
                            else ""
                        ),
                    ),
                )
                published = service.publish(
                    prepared.id, PublishRequest(actor="eval-harness")
                )
                similarities = [item.similarity for item in published.translations]
                if similarities:
                    row["min_similarity"] = round(min(similarities), 4)
                    row["mean_similarity"] = round(
                        sum(similarities) / len(similarities), 4
                    )
                row["translation_count"] = len(similarities)
                row["state"] = published.state.value
                del staged
            except Exception as exc:  # noqa: BLE001 - report failures, then exit non-zero
                row["error"] = f"{type(exc).__name__}: {exc}"
                errors.append(f"{golden.id}: {row['error']}")
            rows.append(row)

        duration = time.perf_counter() - started
        completed = [row for row in rows if not row["error"]]
        # Collect similarities from stored translations without relying on row internals.
        all_similarities: list[float] = []
        for row in rows:
            brief_id = row.get("brief_id")
            if brief_id:
                all_similarities.extend(
                    item.similarity for item in service.get_translations(brief_id)
                )

        judge_scores = [row["judge_score"] for row in completed if row["judge_score"] is not None]
        total = len(rows)
        metrics = {
            "guardrail_pass_rate": round(
                100.0 * sum(1 for row in rows if row["final_passed"]) / total, 1
            ),
            "unsupported_claim_rate": round(
                100.0 * sum(1 for row in rows if row["final_unsupported"]) / total, 1
            ),
            "initial_unsupported_claim_rate": round(
                100.0 * sum(1 for row in rows if row["initial_unsupported"]) / total, 1
            ),
            "brand_voice_rubric_score": (
                round(sum(judge_scores) / len(judge_scores), 3) if judge_scores else None
            ),
            "back_translation_similarity": (
                round(sum(all_similarities) / len(all_similarities), 4)
                if all_similarities
                else None
            ),
            "min_back_translation_similarity": (
                round(min(all_similarities), 4) if all_similarities else None
            ),
            "translation_pass_rate": (
                round(
                    100.0
                    * sum(
                        1
                        for value in all_similarities
                        if value >= settings.translation_similarity_threshold
                    )
                    / len(all_similarities),
                    1,
                )
                if all_similarities
                else None
            ),
            "briefs_revised": sum(1 for row in rows if row["revisions"] > 0),
            "overrides": sum(1 for row in rows if row["override"]),
            "webhook_deliveries": len(container.db.list_webhook_deliveries()),
        }
        result = {
            "meta": {
                "run_id": run_id,
                "generated_at": generated_at,
                "mode": settings.app_mode,
                "commit": _git_commit(),
                "duration_seconds": round(duration, 3),
                "brief_count": len(briefs),
                "max_revision_loops": settings.max_revision_loops,
                "translation_similarity_threshold": settings.translation_similarity_threshold,
                "prompt_versions": container.prompt_versions,
                "briefs_file": str(Path(briefs_path) if briefs_path else GOLDEN_BRIEFS_PATH),
            },
            "metrics": metrics,
            "results": rows,
            "errors": errors,
        }
        result["report_path"] = str(write_report(result, output_dir))
        results_path = Path(output_dir) / "eval_results.json"
        results_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result["results_path"] = str(results_path)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the golden-brief eval harness.")
    parser.add_argument("--briefs", default=None, help="Path to golden briefs YAML.")
    parser.add_argument("--output-dir", default="reports", help="Report output directory.")
    args = parser.parse_args(argv)

    result = run_eval(briefs_path=args.briefs, output_dir=args.output_dir)
    metrics = result["metrics"]
    print("Eval complete (mock mode unless configured otherwise).")
    print(f"  guardrail pass rate: {metrics['guardrail_pass_rate']}%")
    print(f"  unsupported-claim rate (final): {metrics['unsupported_claim_rate']}%")
    print(f"  brand-voice score: {metrics['brand_voice_rubric_score']} / 5")
    print(f"  back-translation similarity: {metrics['back_translation_similarity']}")
    print(f"  report: {result['report_path']}")
    if result["errors"]:
        print("\nErrors:", file=sys.stderr)
        for error in result["errors"]:
            print(f"  - {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
