import hashlib
import inspect
import json
import socket
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.error import HTTPError, URLError

import pytest

from mobiliti_saas.worker.catalog_sync.rates import (
    ExchangeRate,
    fetch_banxico_rates,
    parse_sie_payload,
    persist_rates,
)


UTC_NOW = datetime(2026, 7, 15, 3, 4, 5, tzinfo=timezone.utc)
RAW_HASH = "a" * 64


def _payload(usd="18.5000004", eur="21.0000005"):
    return {
        "bmx": {
            "series": [
                {
                    "idSerie": "SF43718",
                    "datos": [
                        {"fecha": "15/07/2026", "dato": usd},
                        {"fecha": "14/07/2026", "dato": "18.250000"},
                    ],
                },
                {
                    "idSerie": "SF46410",
                    "datos": [
                        {"fecha": "15/07/2026", "dato": eur},
                        {"fecha": "14/07/2026", "dato": "20.750000"},
                    ],
                },
            ]
        }
    }


class FakeHeaders:
    def __init__(self, content_type="application/json"):
        self.content_type = content_type

    def get_content_type(self):
        return self.content_type


class FakeResponse:
    def __init__(self, body, *, status=200, content_type="application/json"):
        self.body = body
        self.status = status
        self.headers = FakeHeaders(content_type)

    def read(self, limit):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class StatuslessResponse:
    def __init__(self, body):
        self.body = body
        self.headers = FakeHeaders()

    def read(self, limit):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FailingContext:
    def __enter__(self):
        raise RuntimeError("super-secret context failure")

    def __exit__(self, *_args):
        return False


def test_parse_maps_both_series_rounds_and_orders_deterministically():
    rows = parse_sie_payload(_payload(), retrieved_at=UTC_NOW, raw_hash=RAW_HASH)

    assert [(row.effective_date, row.currency) for row in rows] == [
        (date(2026, 7, 14), "USD"),
        (date(2026, 7, 14), "EUR"),
        (date(2026, 7, 15), "USD"),
        (date(2026, 7, 15), "EUR"),
    ]
    assert rows[2].mxn_per_unit == Decimal("18.500000")
    assert rows[3].mxn_per_unit == Decimal("21.000001")
    assert (rows[2].series_id, rows[2].source) == ("SF43718", "BANXICO_SIE")
    assert (rows[3].series_id, rows[3].source) == ("SF46410", "BANXICO_SIE")
    assert all(row.retrieved_at == UTC_NOW and row.raw_hash == RAW_HASH for row in rows)
    with pytest.raises(Exception):
        rows[0].currency = "EUR"


def test_parser_normalizes_retrieval_time_to_utc():
    local_time = datetime(2026, 7, 14, 21, 4, 5, tzinfo=timezone(timedelta(hours=-6)))
    rows = parse_sie_payload(_payload(), retrieved_at=local_time, raw_hash=RAW_HASH)
    assert rows[0].retrieved_at == UTC_NOW
    assert rows[0].retrieved_at.tzinfo is timezone.utc


@pytest.mark.parametrize("bad_hash", ["A" * 64, "a" * 63, "g" * 64, 123])
def test_parser_rejects_invalid_raw_hash(bad_hash):
    with pytest.raises(ValueError):
        parse_sie_payload(_payload(), retrieved_at=UTC_NOW, raw_hash=bad_hash)


@pytest.mark.parametrize(
    "fecha",
    ["2026-07-14", "14/7/2026", "1/07/2026", "14/07/26", "31/02/2026"],
)
def test_parser_requires_strict_dd_mm_yyyy(fecha):
    payload = _payload()
    payload["bmx"]["series"][0]["datos"][0]["fecha"] = fecha
    with pytest.raises(ValueError):
        parse_sie_payload(payload, retrieved_at=UTC_NOW, raw_hash=RAW_HASH)


@pytest.mark.parametrize(
    "dato",
    ["N/E", "", " ", "bad", "NaN", "Infinity", "-Infinity", "0", "-1", "0.0000001"],
)
def test_parser_rejects_unusable_rates(dato):
    payload = _payload(usd=dato)
    with pytest.raises(ValueError):
        parse_sie_payload(payload, retrieved_at=UTC_NOW, raw_hash=RAW_HASH)


def test_parser_accepts_numeric_18_6_maximum_after_rounding():
    rows = parse_sie_payload(
        _payload(usd="999999999999.9999994"),
        retrieved_at=UTC_NOW,
        raw_hash=RAW_HASH,
    )
    assert rows[2].mxn_per_unit == Decimal("999999999999.999999")


@pytest.mark.parametrize("dato", ["999999999999.9999995", "1000000000000"])
def test_parser_rejects_rates_outside_numeric_18_6(dato):
    with pytest.raises(ValueError):
        parse_sie_payload(
            _payload(usd=dato), retrieved_at=UTC_NOW, raw_hash=RAW_HASH
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.pop("bmx"),
        lambda payload: payload["bmx"].pop("series"),
        lambda payload: payload["bmx"]["series"].pop(),
        lambda payload: payload["bmx"]["series"][0].update(idSerie="UNKNOWN"),
        lambda payload: payload["bmx"]["series"].append(payload["bmx"]["series"][0]),
        lambda payload: payload["bmx"]["series"][0].pop("datos"),
    ],
    ids=["missing-bmx", "missing-series", "missing-approved-series", "unknown", "duplicate-series", "missing-data"],
)
def test_parser_rejects_malformed_series_shape(mutate):
    payload = _payload()
    mutate(payload)
    with pytest.raises(ValueError):
        parse_sie_payload(payload, retrieved_at=UTC_NOW, raw_hash=RAW_HASH)


def test_parser_deduplicates_identical_observation_but_rejects_conflict():
    payload = _payload()
    observation = payload["bmx"]["series"][0]["datos"][0]
    payload["bmx"]["series"][0]["datos"].append(dict(observation))
    assert len(parse_sie_payload(payload, retrieved_at=UTC_NOW, raw_hash=RAW_HASH)) == 4

    payload["bmx"]["series"][0]["datos"][-1]["dato"] = "19.000000"
    with pytest.raises(ValueError):
        parse_sie_payload(payload, retrieved_at=UTC_NOW, raw_hash=RAW_HASH)


def test_fetch_builds_official_request_and_hashes_exact_bytes(monkeypatch):
    body = json.dumps(_payload(), separators=(",", ":")).encode()
    seen = {}

    def transport(request, timeout):
        seen.update(request=request, timeout=timeout)
        return FakeResponse(body)

    monkeypatch.setattr("mobiliti_saas.worker.catalog_sync.rates._utc_now", lambda: UTC_NOW)
    rows = fetch_banxico_rates(transport, "super-secret", date(2026, 7, 14), date(2026, 7, 15))

    request = seen["request"]
    assert request.full_url == (
        "https://www.banxico.org.mx/SieAPIRest/service/v1/series/"
        "SF43718,SF46410/datos/2026-07-14/2026-07-15"
    )
    assert request.get_header("Bmx-token") == "super-secret"
    assert "super-secret" not in request.full_url
    assert seen["timeout"] > 0
    assert all(row.raw_hash == hashlib.sha256(body).hexdigest() for row in rows)
    assert all(row.retrieved_at == UTC_NOW for row in rows)


@pytest.mark.parametrize("token", ["", " ", None])
def test_fetch_rejects_missing_token_without_calling_transport(token):
    def transport(*_args, **_kwargs):
        raise AssertionError("transport must not be called")

    with pytest.raises(ValueError):
        fetch_banxico_rates(transport, token, date(2026, 7, 14), date(2026, 7, 15))


@pytest.mark.parametrize(
    "start,end",
    [
        (date(2026, 7, 15), date(2026, 7, 14)),
        (date(2025, 7, 14), date(2026, 7, 15)),
    ],
)
def test_fetch_rejects_invalid_or_unbounded_date_ranges(start, end):
    with pytest.raises(ValueError):
        fetch_banxico_rates(lambda *_args, **_kwargs: None, "token", start, end)


def test_fetch_rejects_non_https_or_non_banxico_endpoint(monkeypatch):
    monkeypatch.setattr(
        "mobiliti_saas.worker.catalog_sync.rates._URL_TEMPLATE",
        "http://attacker.invalid/{start}/{end}",
    )
    with pytest.raises(ValueError, match="endpoint"):
        fetch_banxico_rates(
            lambda *_args, **_kwargs: None,
            "token",
            date(2026, 7, 14),
            date(2026, 7, 15),
        )


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(b"not-json"),
        FakeResponse(b"{}", status=500),
        FakeResponse(b"{}", content_type="text/html"),
        FakeResponse(b"x" * (1_048_576 + 1)),
    ],
    ids=["malformed-json", "bad-status", "bad-content-type", "too-large"],
)
def test_fetch_rejects_bad_responses(response):
    with pytest.raises(ValueError):
        fetch_banxico_rates(lambda *_args, **_kwargs: response, "token", date(2026, 7, 14), date(2026, 7, 15))


def test_fetch_requires_status_to_confirm_exactly_200():
    body = json.dumps(_payload()).encode()
    with pytest.raises(ValueError, match="Banxico request failed"):
        fetch_banxico_rates(
            lambda *_args, **_kwargs: StatuslessResponse(body),
            "token",
            date(2026, 7, 14),
            date(2026, 7, 15),
        )


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("super-secret timed out"),
        socket.timeout("super-secret timed out"),
        URLError("super-secret refused"),
        HTTPError("https://example.invalid/super-secret", 401, "super-secret denied", {}, None),
        RuntimeError("super-secret runtime failure"),
    ],
)
def test_fetch_redacts_transport_failures(error):
    def transport(*_args, **_kwargs):
        raise error

    with pytest.raises(ValueError) as caught:
        fetch_banxico_rates(transport, "super-secret", date(2026, 7, 14), date(2026, 7, 15))
    assert "super-secret" not in str(caught.value)
    assert str(caught.value) == "Banxico request failed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_fetch_redacts_context_manager_failure():
    with pytest.raises(ValueError) as caught:
        fetch_banxico_rates(
            lambda *_args, **_kwargs: FailingContext(),
            "super-secret",
            date(2026, 7, 14),
            date(2026, 7, 15),
        )
    assert str(caught.value) == "Banxico request failed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_fetch_does_not_capture_base_exception():
    def transport(*_args, **_kwargs):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        fetch_banxico_rates(
            transport, "token", date(2026, 7, 14), date(2026, 7, 15)
        )


def test_malformed_json_does_not_survive_in_exception_chain():
    body = b'{"authorization":"private-response-body"'
    with pytest.raises(ValueError) as caught:
        fetch_banxico_rates(
            lambda *_args, **_kwargs: FakeResponse(body),
            "token",
            date(2026, 7, 14),
            date(2026, 7, 15),
        )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "private-response-body" not in repr(caught.value)


class FakeRepository:
    def __init__(self, rows=()):
        self.rows = {(row.currency, row.effective_date): row for row in rows}
        self.inserted = []
        self.calls = []

    def insert_rates_if_absent(self, rates):
        rates = tuple(rates)
        self.calls.append(rates)
        missing = []
        for rate in rates:
            key = (rate.currency, rate.effective_date)
            existing = self.rows.get(key)
            if existing is None:
                missing.append(rate)
            elif existing != rate:
                raise ValueError("Conflicting exchange rate")
        for rate in missing:
            self.rows[(rate.currency, rate.effective_date)] = rate
            self.inserted.append(rate)
        return len(missing)


def _rate(value="18.500000", raw_hash=RAW_HASH):
    return ExchangeRate(
        currency="USD",
        effective_date=date(2026, 7, 14),
        mxn_per_unit=Decimal(value),
        series_id="SF43718",
        source="BANXICO_SIE",
        retrieved_at=UTC_NOW,
        raw_hash=raw_hash,
    )


def test_persist_rates_is_append_only_and_idempotent():
    rate = _rate()
    repository = FakeRepository()
    assert persist_rates(repository, [rate]) == 1
    assert repository.inserted == [rate]
    assert persist_rates(repository, [rate, rate]) == 0
    assert repository.inserted == [rate]
    assert repository.calls == [(rate,), (rate,)]


def test_persist_rates_rejects_conflicts_before_any_insert():
    existing = _rate()
    repository = FakeRepository([existing])
    new_day = ExchangeRate(**{**existing.__dict__, "effective_date": date(2026, 7, 15)})
    conflict = _rate(value="19.000000")

    with pytest.raises(ValueError):
        persist_rates(repository, [new_day, conflict])
    assert repository.inserted == []
    assert repository.calls == [(new_day, conflict)]
    assert repository.rows[("USD", date(2026, 7, 14))] == existing


def test_module_uses_stdlib_transport_and_never_opens_a_socket_by_itself():
    import mobiliti_saas.worker.catalog_sync.rates as rates

    source = inspect.getsource(rates)
    assert "requests" not in source
    assert "socket." not in source
