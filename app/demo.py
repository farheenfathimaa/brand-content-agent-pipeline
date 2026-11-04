"""Zero-key demo: run a clean brief and a stress-test brief through the pipeline."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.container import build_container
from app.evals.runner import load_golden_briefs
from app.schemas import ApprovalRequest, BriefCreate, PublishRequest


def main() -> int:
    settings = Settings(
        _env_file=None,
        app_mode="mock",
        database_path="data/demo.db",
        webhook_url="",
        task_tracker="mock",
        generate_design_assets=False,
    )
    container = build_container(settings)
    service = container.service
    golden = {brief.id: brief for brief in load_golden_briefs()}

    print("Mode:", settings.app_mode)
    print("Brand: Alder Grove (fictional)\n")

    clean = golden["gb-01"]
    brief = service.create_brief(
        BriefCreate(
            title=clean.title,
            vertical=clean.vertical,
            brief=clean.brief,
            fact_sheet_id=clean.fact_sheet_id,
            languages=clean.languages,
            metadata={"stress_test": clean.stress_test},
        )
    )
    print(f"[clean] {brief.id} state={brief.state.value} revisions={brief.revision_count}")
    print(f"        guardrails_passed={brief.guardrail.passed if brief.guardrail else None}")
    staged = service.approve(
        brief.id, ApprovalRequest(approver="demo@example.com", notes="Looks good.")
    )
    print(f"[clean] approved state={staged.state.value} translations={len(staged.translations)}")
    for translation in staged.translations:
        print(
            f"        {translation.language.value}: similarity={translation.similarity:.3f}"
        )
    published = service.publish(brief.id, PublishRequest(actor="demo@example.com"))
    print(f"[clean] published state={published.state.value} cms_docs={len(published.cms_documents)}")
    if published.cms_documents:
        doc = published.cms_documents[0]
        print(f"        preview: /api/v1/cms/{doc.id}/preview")
    print()

    stressed = golden["gb-07"]
    stressed_brief = service.create_brief(
        BriefCreate(
            title=stressed.title,
            vertical=stressed.vertical,
            brief=stressed.brief,
            fact_sheet_id=stressed.fact_sheet_id,
            languages=stressed.languages,
            metadata={"stress_test": stressed.stress_test},
        )
    )
    report = stressed_brief.guardrail
    initial = stressed_brief.initial_guardrail
    print(
        f"[stress] {stressed_brief.id} state={stressed_brief.state.value} "
        f"revisions={stressed_brief.revision_count} "
        f"initial_passed={initial.passed if initial else None} "
        f"final_passed={report.passed if report else None}"
    )
    for violation in report.violations if report else []:
        print(f"        remaining: {violation.rule_id} ({violation.severity.value})")
    approved = service.approve(
        stressed_brief.id,
        ApprovalRequest(approver="demo@example.com", notes="Revision loop cleaned it up."),
    )
    print(f"[stress] approved state={approved.state.value}")
    print(f"        database: {Path(settings.database_path).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
