from __future__ import annotations

import json
from html.parser import HTMLParser

import pytest

from proteus.report import write_report


class AuditTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_audit_body = False
        self.cells: list[str] = []
        self.links: list[str] = []
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag, attrs) -> None:
        values = dict(attrs)
        if tag == "tbody" and values.get("id") == "audit-rows":
            self.in_audit_body = True
        elif self.in_audit_body and tag == "td":
            self._cell_parts = []
        elif self.in_audit_body and tag == "a" and "href" in values:
            self.links.append(values["href"])

    def handle_endtag(self, tag) -> None:
        if tag == "tbody" and self.in_audit_body:
            self.in_audit_body = False
        elif tag == "td" and self._cell_parts is not None:
            self.cells.append("".join(self._cell_parts).strip())
            self._cell_parts = None

    def handle_data(self, data) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)


def test_report_has_hidden_optional_audit_section(tmp_path) -> None:
    html = write_report(tmp_path).read_text()

    assert 'id="audit-section" hidden' in html
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
    parser = AuditTableParser()
    parser.feed(html)

    assert "Safety audits" in html
    assert 'id="audit-rows"' in html
    assert 'id="tbl"' in html
    assert "last score" in html
    assert 'id="audit-section" hidden' not in html
    assert parser.cells[:5] == [
        "integrity",
        "instrument-integrity 1",
        "not_evaluated:1  pass:3",
        "trace:4",
        "artifact:4",
    ]
    assert parser.cells[5] == "completed"
    assert parser.links == [
        "audits/integrity/summary.json",
        "audits/integrity/results.jsonl",
    ]


def test_report_skips_malformed_entries_and_escapes_text(tmp_path) -> None:
    audit = tmp_path / "audits/good"
    audit.mkdir(parents=True)
    (audit / "summary.json").write_text(
        json.dumps(
            {
                "status_counts": {"pass": 1},
                "target_counts": {"trace": 1},
                "evidence_method_counts": {"artifact": 1},
            }
        )
    )
    (tmp_path / "audits/index.json").write_text(
        json.dumps(
            {
                "audits": [
                    None,
                    {
                        "id": "good",
                        "suite": "<script>alert(1)</script>",
                        "version": "1",
                        "summary": "good/summary.json",
                        "results": "good/results&details.jsonl",
                    },
                ]
            }
        )
    )

    html = write_report(tmp_path).read_text()
    parser = AuditTableParser()
    parser.feed(html)

    assert parser.cells[0] == "good"
    assert parser.cells[1] == "<script>alert(1)</script> 1"
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "results&amp;details.jsonl" in html
    assert parser.links[-1] == "audits/good/results&details.jsonl"


@pytest.mark.parametrize("index_text", ["not-json", "[]", '{"audits": "bad"}'])
def test_report_hides_audits_for_malformed_index(tmp_path, index_text: str) -> None:
    audits = tmp_path / "audits"
    audits.mkdir()
    (audits / "index.json").write_text(index_text)

    html = write_report(tmp_path).read_text()

    assert 'id="audit-section" hidden' in html
    parser = AuditTableParser()
    parser.feed(html)
    assert parser.cells == []


@pytest.mark.parametrize("summary_text", [None, "not-json", "[]"])
def test_report_skips_missing_or_malformed_summary(
    tmp_path, summary_text: str | None
) -> None:
    audit = tmp_path / "audits/bad"
    audit.mkdir(parents=True)
    if summary_text is not None:
        (audit / "summary.json").write_text(summary_text)
    (tmp_path / "audits/index.json").write_text(
        json.dumps(
            {
                "audits": [
                    {
                        "id": "bad",
                        "suite": "fixture",
                        "version": "1",
                        "summary": "bad/summary.json",
                        "results": "bad/results.jsonl",
                    }
                ]
            }
        )
    )

    html = write_report(tmp_path).read_text()

    assert 'id="audit-section" hidden' in html
    parser = AuditTableParser()
    parser.feed(html)
    assert parser.cells == []
