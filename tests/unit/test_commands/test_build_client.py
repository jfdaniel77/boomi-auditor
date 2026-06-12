"""build_client progress-mode selection: table vs piped json/csv vs --output file."""

from boomi_auditor.commands import build_client


class TestShowProgress:
    def test_table_mode_shows_progress(self):
        with build_client(fmt="table") as client:
            assert client.show_progress

    def test_json_pipe_hides_progress(self):
        with build_client(fmt="json") as client:
            assert not client.show_progress

    def test_csv_pipe_hides_progress(self):
        with build_client(fmt="csv") as client:
            assert not client.show_progress

    def test_file_output_keeps_progress(self, tmp_path):
        """--output writes the report to a file, so stdout isn't the report
        stream — a long audit must not run silent just because fmt is csv."""
        with build_client(fmt="csv", output=tmp_path / "report.csv") as client:
            assert client.show_progress
