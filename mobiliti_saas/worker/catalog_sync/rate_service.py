import argparse
import os
import urllib.request
from datetime import date, timedelta

from .rates import fetch_banxico_rates, persist_rates
from .repository import CatalogRepository


RATE_EXIT_WORKED = 0
RATE_EXIT_FAILED = 1
RATE_EXIT_NO_WORK = 2
RATE_EXIT_DISABLED = 3
_LOOKBACK_DAYS = 14


def run_rate_sync_once(*, repository=None, transport=None, token=None, today=None):
    token = os.environ.get("BANXICO_SIE_TOKEN", "") if token is None else token
    if not isinstance(token, str) or not token.strip():
        return "disabled"
    token = token.strip()
    today = date.today() if today is None else today
    if type(today) is not date:
        raise ValueError("Invalid rate sync date")
    repository = repository or CatalogRepository.from_environment()
    transport = transport or urllib.request.urlopen
    rates = fetch_banxico_rates(
        transport, token, today - timedelta(days=_LOOKBACK_DAYS - 1), today
    )
    return "worked" if persist_rates(repository, rates) else "no_work"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Refresh official Banxico exchange rates")
    parser.parse_args(argv)
    try:
        status = run_rate_sync_once()
    except Exception:
        return RATE_EXIT_FAILED
    return {
        "worked": RATE_EXIT_WORKED,
        "no_work": RATE_EXIT_NO_WORK,
        "disabled": RATE_EXIT_DISABLED,
    }.get(status, RATE_EXIT_FAILED)


if __name__ == "__main__":
    raise SystemExit(main())
