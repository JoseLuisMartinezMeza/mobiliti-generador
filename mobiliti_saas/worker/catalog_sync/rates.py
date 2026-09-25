import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from urllib.parse import urlparse


_SERIES = {"SF43718": "USD", "SF46410": "EUR"}
_SERIES_IDS = ",".join(_SERIES)
_URL_TEMPLATE = (
    "https://www.banxico.org.mx/SieAPIRest/service/v1/series/"
    f"{_SERIES_IDS}/datos/{{start}}/{{end}}"
)
_SOURCE = "BANXICO_SIE"
_TIMEOUT_SECONDS = 10
_MAX_RANGE_DAYS = 365
_MAX_RESPONSE_BYTES = 1_048_576
_SIX_PLACES = Decimal("0.000001")
_MAX_RATE = Decimal("999999999999.999999")
_RAW_HASH = re.compile(r"[0-9a-f]{64}")
_SIE_DATE = re.compile(r"\d{2}/\d{2}/\d{4}")


@dataclass(frozen=True)
class ExchangeRate:
    currency: str
    effective_date: date
    mxn_per_unit: Decimal
    series_id: str
    source: str
    retrieved_at: datetime
    raw_hash: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("Invalid retrieval timestamp")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _date(value: object) -> date:
    if not isinstance(value, str) or _SIE_DATE.fullmatch(value) is None:
        raise ValueError("Invalid Banxico date")
    try:
        parsed = datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError as error:
        raise ValueError("Invalid Banxico date") from error
    if parsed.strftime("%d/%m/%Y") != value:
        raise ValueError("Invalid Banxico date")
    return parsed


def _rate(value: object) -> Decimal:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("Invalid Banxico rate")
    try:
        number = Decimal(value)
        if not number.is_finite() or number <= 0:
            raise ValueError("Invalid Banxico rate")
        with localcontext() as context:
            context.prec = max(28, len(number.as_tuple().digits) + 7)
            rounded = number.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
            if rounded <= 0 or rounded > _MAX_RATE:
                raise ValueError("Invalid Banxico rate")
            return rounded
    except InvalidOperation as error:
        raise ValueError("Invalid Banxico rate") from error


def parse_sie_payload(
    payload: dict, *, retrieved_at: datetime, raw_hash: str
) -> list[ExchangeRate]:
    if not isinstance(raw_hash, str) or _RAW_HASH.fullmatch(raw_hash) is None:
        raise ValueError("Invalid response hash")
    retrieved_at = _utc(retrieved_at)
    if not isinstance(payload, dict):
        raise ValueError("Invalid Banxico payload")
    bmx = payload.get("bmx")
    series = bmx.get("series") if isinstance(bmx, dict) else None
    if not isinstance(series, list):
        raise ValueError("Invalid Banxico payload")

    seen_series: set[str] = set()
    observations: dict[tuple[str, date], ExchangeRate] = {}
    for item in series:
        if not isinstance(item, dict):
            raise ValueError("Invalid Banxico series")
        series_id = item.get("idSerie")
        if series_id not in _SERIES or series_id in seen_series:
            raise ValueError("Unknown or duplicate Banxico series")
        data = item.get("datos")
        if not isinstance(data, list) or not data:
            raise ValueError("Invalid Banxico series")
        seen_series.add(series_id)
        currency = _SERIES[series_id]
        for item_data in data:
            if not isinstance(item_data, dict):
                raise ValueError("Invalid Banxico observation")
            effective_date = _date(item_data.get("fecha"))
            mxn_per_unit = _rate(item_data.get("dato"))
            row = ExchangeRate(
                currency=currency,
                effective_date=effective_date,
                mxn_per_unit=mxn_per_unit,
                series_id=series_id,
                source=_SOURCE,
                retrieved_at=retrieved_at,
                raw_hash=raw_hash,
            )
            key = (currency, effective_date)
            existing = observations.get(key)
            if existing is not None and existing != row:
                raise ValueError("Conflicting Banxico observation")
            observations[key] = row

    if seen_series != set(_SERIES):
        raise ValueError("Missing Banxico series")
    currency_order = {"USD": 0, "EUR": 1}
    return sorted(
        observations.values(),
        key=lambda row: (row.effective_date, currency_order[row.currency]),
    )


def _content_type(response) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    getter = getattr(headers, "get_content_type", None)
    if callable(getter):
        return getter()
    value = headers.get("Content-Type") if hasattr(headers, "get") else None
    return value.split(";", 1)[0].strip().lower() if isinstance(value, str) else None


def fetch_banxico_rates(
    transport, token: str, start: date, end: date
) -> list[ExchangeRate]:
    if not isinstance(token, str) or not token.strip():
        raise ValueError("Banxico token is required")
    if type(start) is not date or type(end) is not date:
        raise ValueError("Invalid date range")
    if start > end or (end - start).days > _MAX_RANGE_DAYS:
        raise ValueError("Invalid date range")
    url = _URL_TEMPLATE.format(start=start.isoformat(), end=end.isoformat())
    parsed_url = urlparse(url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "www.banxico.org.mx"
        or parsed_url.port not in (None, 443)
        or parsed_url.username is not None
        or parsed_url.password is not None
    ):
        raise ValueError("Invalid Banxico endpoint")
    request = urllib.request.Request(url, headers={"Bmx-Token": token})

    request_failed = False
    try:
        with transport(request, timeout=_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None)
            if status is None and hasattr(response, "getcode"):
                status = response.getcode()
            if type(status) is not int or status != 200:
                raise ValueError("Invalid Banxico response")
            content_type = _content_type(response)
            if content_type is not None and not (
                content_type == "application/json" or content_type.endswith("+json")
            ):
                raise ValueError("Invalid Banxico response")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except Exception:
        request_failed = True
    if request_failed:
        raise ValueError("Banxico request failed")

    if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("Invalid Banxico response")
    raw_hash = hashlib.sha256(raw).hexdigest()
    invalid_json = False
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        invalid_json = True
    if invalid_json:
        raise ValueError("Invalid Banxico response")
    return parse_sie_payload(payload, retrieved_at=_utc_now(), raw_hash=raw_hash)


def persist_rates(repository, rates) -> int:
    unique: dict[tuple[str, date], ExchangeRate] = {}
    for rate in rates:
        if not isinstance(rate, ExchangeRate):
            raise ValueError("Invalid exchange rate")
        key = (rate.currency, rate.effective_date)
        duplicate = unique.get(key)
        if duplicate is not None and duplicate != rate:
            raise ValueError("Conflicting exchange rate")
        unique[key] = rate

    if not unique:
        return 0
    return repository.insert_rates_if_absent(tuple(unique.values()))
