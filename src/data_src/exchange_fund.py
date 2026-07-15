"""Exchange-traded fund classification helpers."""

from __future__ import annotations

from dataclasses import dataclass

from .common import infer_a_share_exchange, normalize_a_share_code


EXCHANGE_FUND_ETF = "ETF"
EXCHANGE_FUND_LOF = "LOF"
EXCHANGE_FUND_CLOSED = "CLOSED_END"
EXCHANGE_FUND_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ExchangeFundIdentity:
    """Best-effort exchange-traded fund identity."""

    symbol: str
    exchange: str
    fund_type: str
    confidence: str
    reason: str


def identify_exchange_fund(
    symbol: str,
    name: str | None = None,
    type_hint: str | None = None,
) -> ExchangeFundIdentity:
    """Identify ETF, LOF, or closed-end fund by hints and code prefix.

    Name and provider type hints take priority. Code prefixes are only a
    fallback because exchange rules and product naming can change over time.
    """

    code = normalize_a_share_code(symbol)
    exchange = infer_a_share_exchange(code)
    text = f"{name or ''} {type_hint or ''}".upper()

    if "ETF" in text:
        return ExchangeFundIdentity(code, exchange, EXCHANGE_FUND_ETF, "high", "name_or_type_contains_etf")
    if "LOF" in text:
        return ExchangeFundIdentity(code, exchange, EXCHANGE_FUND_LOF, "high", "name_or_type_contains_lof")
    if any(keyword in text for keyword in ("封闭", "封基", "CLOSED")):
        return ExchangeFundIdentity(code, exchange, EXCHANGE_FUND_CLOSED, "high", "name_or_type_contains_closed")

    if code.startswith(("51", "56", "58", "15")):
        return ExchangeFundIdentity(code, exchange, EXCHANGE_FUND_ETF, "medium", "common_etf_code_prefix")
    if code.startswith(("16", "18")):
        return ExchangeFundIdentity(code, exchange, EXCHANGE_FUND_LOF, "medium", "common_lof_code_prefix")
    if code.startswith("50"):
        return ExchangeFundIdentity(code, exchange, EXCHANGE_FUND_CLOSED, "low", "legacy_closed_fund_prefix")

    return ExchangeFundIdentity(code, exchange, EXCHANGE_FUND_UNKNOWN, "low", "no_rule_matched")


def to_exchange_symbol(symbol: str) -> str:
    """Return exchange-prefixed symbol such as `sh510300` or `sz159915`."""

    code = normalize_a_share_code(symbol)
    return f"{infer_a_share_exchange(code)}{code}"
