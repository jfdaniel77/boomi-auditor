"""Formatter tests: table, json, csv, and the file-overwrite prompt."""

from __future__ import annotations

import json

from boomi_auditor import formatters

ROWS = [
    {"name": "SAP HTTP Client", "severity": "warning", "environments": ["DEV", "PROD"]},
    {"name": "Oracle DB", "severity": "info", "extra": {"k": "v"}},
]


class TestJson:
    def test_round_trip(self):
        assert json.loads(formatters.to_json(ROWS))[0]["name"] == "SAP HTTP Client"

    def test_render_returns_content(self, capsys):
        content = formatters.render(ROWS, fmt="json")
        assert json.loads(content) == json.loads(capsys.readouterr().out)


class TestCsv:
    def test_header_is_union_of_keys(self):
        lines = formatters.to_csv(ROWS).splitlines()
        assert lines[0] == "name,severity,environments,extra"

    def test_lists_and_dicts_flattened(self):
        content = formatters.to_csv(ROWS)
        assert "DEV; PROD" in content
        assert '""k"": ""v""' in content  # dict serialized as quoted JSON

    def test_empty_rows(self):
        assert formatters.to_csv([]) == ""

    def test_render_prints_csv(self, capsys):
        formatters.render(ROWS, fmt="csv")
        assert capsys.readouterr().out.startswith("name,severity")


class TestTable:
    def test_render_table_returns_none(self, capsys):
        assert formatters.render(ROWS, fmt="table", title="Findings") is None
        assert "SAP HTTP Client" in capsys.readouterr().out

    def test_empty_table_shows_no_findings(self, capsys):
        formatters.render([], fmt="table")
        assert "No findings" in capsys.readouterr().out


class TestWriteOutput:
    def test_writes_new_file(self, tmp_path):
        target = tmp_path / "report.csv"
        assert formatters.write_output("a,b\n", target) is True
        assert target.read_text() == "a,b\n"

    def test_force_overwrites_existing(self, tmp_path):
        target = tmp_path / "report.csv"
        target.write_text("old")
        assert formatters.write_output("new", target, force=True) is True
        assert target.read_text() == "new"

    def test_prompt_declined_keeps_file(self, tmp_path, monkeypatch):
        target = tmp_path / "report.csv"
        target.write_text("old")
        monkeypatch.setattr("boomi_auditor.formatters.Confirm.ask", lambda *a, **k: False)
        assert formatters.write_output("new", target) is False
        assert target.read_text() == "old"

    def test_prompt_accepted_overwrites(self, tmp_path, monkeypatch):
        target = tmp_path / "report.csv"
        target.write_text("old")
        monkeypatch.setattr("boomi_auditor.formatters.Confirm.ask", lambda *a, **k: True)
        assert formatters.write_output("new", target) is True
        assert target.read_text() == "new"
