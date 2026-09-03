from datetime import date

import pytest

from mobiliti_saas.worker.catalog_sync import rate_service


class Repository:
    def __init__(self, inserted=1):
        self.inserted = inserted
        self.rates = None

    def insert_rates_if_absent(self, rates):
        self.rates = rates
        return self.inserted


def test_rate_service_fetches_bounded_window_and_persists(monkeypatch):
    repository = Repository()
    seen = {}
    rows = [object()]

    def fetch(transport, token, start, end):
        seen.update(transport=transport, token=token, start=start, end=end)
        return rows

    monkeypatch.setattr(rate_service, "fetch_banxico_rates", fetch)
    monkeypatch.setattr(
        rate_service, "persist_rates", lambda current, rates: current.inserted
    )
    transport = object()

    assert rate_service.run_rate_sync_once(
        repository=repository,
        transport=transport,
        token="private-token",
        today=date(2026, 7, 17),
    ) == "worked"
    assert seen == {
        "transport": transport,
        "token": "private-token",
        "start": date(2026, 7, 4),
        "end": date(2026, 7, 17),
    }


def test_rate_service_is_disabled_without_token(monkeypatch):
    monkeypatch.delenv("BANXICO_SIE_TOKEN", raising=False)
    monkeypatch.setattr(
        rate_service.CatalogRepository,
        "from_environment",
        lambda: pytest.fail("repository must not initialize"),
    )
    assert rate_service.run_rate_sync_once() == "disabled"


@pytest.mark.parametrize(("status", "code"), [
    ("worked", rate_service.RATE_EXIT_WORKED),
    ("no_work", rate_service.RATE_EXIT_NO_WORK),
    ("disabled", rate_service.RATE_EXIT_DISABLED),
    ("invalid", rate_service.RATE_EXIT_FAILED),
])
def test_rate_service_cli_has_bounded_exit_protocol(monkeypatch, status, code):
    monkeypatch.setattr(rate_service, "run_rate_sync_once", lambda: status)
    assert rate_service.main([]) == code


def test_rate_service_cli_redacts_failures(monkeypatch, capsys):
    monkeypatch.setattr(
        rate_service,
        "run_rate_sync_once",
        lambda: (_ for _ in ()).throw(RuntimeError("private-token")),
    )
    assert rate_service.main([]) == rate_service.RATE_EXIT_FAILED
    captured = capsys.readouterr()
    assert "private-token" not in captured.out + captured.err
