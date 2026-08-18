from __future__ import annotations

import json

from proteus.report import write_report


def test_report_has_hidden_optional_audit_section(tmp_path) -> None:
    html = write_report(tmp_path).read_text()

    assert 'id="audit-section" hidden' in html
    assert "audits/index.json" in html
    assert "post-run evidence; never fed back into evolution" in html
    assert "composite safety score" not in html.lower()


def test_report_uses_a_separate_audit_table(tmp_path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "name": "fixture",
                "episodes": 1,
                "arms": [],
                "seeds": 0,
                "runs": [],
            }
        )
    )
    audit = tmp_path / "audits/integrity"
    audit.mkdir(parents=True)
    (audit / "summary.json").write_text(
        json.dumps(
            {
                "status_counts": {"pass": 3, "not_evaluated": 1},
                "target_counts": {"trace": 4},
                "evidence_method_counts": {"artifact": 4},
            }
        )
    )
    (tmp_path / "audits/index.json").write_text(
        json.dumps(
            {
                "audits": [
                    {
                        "id": "integrity",
                        "suite": "instrument-integrity",
                        "version": "1",
                        "summary": "integrity/summary.json",
                        "results": "integrity/results.jsonl",
                    }
                ]
            }
        )
    )

    html = write_report(tmp_path).read_text()

    assert "Safety audits" in html
    assert 'id="audit-rows"' in html
    assert 'id="tbl"' in html
    assert "last score" in html
    assert "textContent" in html
