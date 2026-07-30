from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tradebot.data import crypto_external_factors_hyperliquid as base
from tradebot.data.crypto_external_factors import ExternalFactorDataError

MIN_REQUEST_INTERVAL_SECONDS = 1.10
MAX_RETRIES = 8


def _retry_delay(exc: Exception, attempt: int) -> float:
    if isinstance(exc, HTTPError) and exc.code == 429:
        raw = exc.headers.get("Retry-After") if exc.headers is not None else None
        try:
            if raw is not None:
                return max(1.0, min(float(raw), 120.0))
        except ValueError:
            pass
        return min(15.0 * (2 ** (attempt - 1)), 120.0)
    return min(2.0 * attempt, 15.0)


def _rate_limited_post_json(
    body: dict[str, Any],
    *,
    timeout: float = 45.0,
    retries: int = MAX_RETRIES,
) -> Any:
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        # The public endpoint applies weighted IP limits. Historical pages are
        # deliberately serialized so hosted runners do not burst through them.
        time.sleep(MIN_REQUEST_INTERVAL_SECONDS)
        request = Request(
            base.HYPERLIQUID_INFO_URL,
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "crypt-and-share-market-paper-research/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public API URL
                if response.status != 200:
                    raise ExternalFactorDataError(
                        f"Hyperliquid returned HTTP {response.status}"
                    )
                return json.loads(response.read().decode("utf-8"))
        except (
            HTTPError,
            URLError,
            TimeoutError,
            json.JSONDecodeError,
            ExternalFactorDataError,
        ) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(_retry_delay(exc, attempt))
    raise ExternalFactorDataError(
        f"Rate-limited Hyperliquid request failed: {last_error}"
    )


def main(argv: list[str] | None = None) -> int:
    # Patch only the transport hook. Parsing, filtering, hashing, provider
    # metadata and the frozen evaluator-facing CSV contract remain unchanged.
    base._post_json = _rate_limited_post_json
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
