import sys
from types import SimpleNamespace

from app.rag import cli
from app.rag.config import TableRagSettings


class FakeService:
    async def ainvoke(self, question, *, request_id):
        return SimpleNamespace(
            response=SimpleNamespace(
                model_dump=lambda **kwargs: {
                    "question": question,
                    "request_id": request_id,
                }
            )
        )

    async def aclose(self):
        pass


def test_cli_resolves_explicit_directory_and_uses_resolved_run_directory(
    monkeypatch, tmp_path, capsys
):
    settings = TableRagSettings(
        data_directory=tmp_path / "data",
        run_directory=tmp_path / "runs",
    )
    captured = {}

    def resolve_settings(*, data_directory):
        captured["cli_data_directory"] = data_directory
        return settings

    async def load_service(*, settings):
        captured["settings"] = settings
        return FakeService()

    monkeypatch.setattr(sys, "argv", ["table-rag", "예약 확인", "--data-directory", str(tmp_path / "data")])
    monkeypatch.setattr(cli, "load_table_rag_settings", resolve_settings)
    monkeypatch.setattr(cli, "load_service", load_service)

    cli.main()

    output = capsys.readouterr().out
    assert captured["cli_data_directory"] == str(tmp_path / "data")
    assert captured["settings"] is settings
    assert f"Saved run: {tmp_path / 'runs'}" in output
