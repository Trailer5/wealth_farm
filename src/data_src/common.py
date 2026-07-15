"""Common helpers for external market data adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class DataSourceError(RuntimeError):
    """Raised when an external data source call fails."""


class DependencyNotInstalledError(DataSourceError):
    """Raised when a provider package is not installed."""


def infer_a_share_exchange(symbol: str) -> str:
    """Infer A-share exchange prefix from a stock code."""

    code = normalize_a_share_code(symbol)
    if code.startswith(("5", "6", "9")):
        return "sh"
    if code.startswith(("0", "1", "2", "3")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    raise ValueError(f"Unsupported A-share code prefix: {symbol}")


def normalize_a_share_code(symbol: str) -> str:
    """Return the six-digit A-share code without exchange prefix."""

    value = symbol.strip()
    if not value:
        raise ValueError("symbol must not be empty")

    lowered = value.lower()
    for prefix in ("sh.", "sz.", "bj."):
        if lowered.startswith(prefix):
            value = value[3:]
            break

    upper = value.upper()
    for suffix in (".SH", ".SZ", ".BJ"):
        if upper.endswith(suffix):
            value = value[:-3]
            break

    compact = value.replace(".", "").strip()
    if len(compact) != 6 or not compact.isdigit():
        raise ValueError(f"Unsupported A-share symbol format: {symbol}")
    return compact


def to_akshare_symbol(symbol: str) -> str:
    """Return the symbol format expected by AkShare A-share history APIs."""

    return normalize_a_share_code(symbol)


def to_akshare_market_symbol(symbol: str) -> str:
    """Return the symbol format used by AkShare APIs that require market suffix."""

    code = normalize_a_share_code(symbol)
    exchange = infer_a_share_exchange(code)
    suffix_by_exchange = {"sh": "SH", "sz": "SZ", "bj": "BJ"}
    return f"{code}.{suffix_by_exchange[exchange]}"


def to_baostock_symbol(symbol: str) -> str:
    """Return the symbol format expected by BaoStock A-share APIs."""

    code = normalize_a_share_code(symbol)
    exchange = infer_a_share_exchange(code)
    if exchange == "bj":
        raise ValueError("BaoStock adapter does not support Beijing Stock Exchange codes yet")
    return f"{exchange}.{code}"


def normalize_date_compact(value: str) -> str:
    """Return a date as YYYYMMDD."""

    return _parse_date(value).strftime("%Y%m%d")


def normalize_date_dash(value: str) -> str:
    """Return a date as YYYY-MM-DD."""

    return _parse_date(value).strftime("%Y-%m-%d")


def to_optional_float(value: Any) -> float | None:
    """Convert provider values to float while preserving blanks as None."""

    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        value = stripped.replace(",", "")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_date_string(value: Any) -> str:
    """Convert provider date values to YYYY-MM-DD when possible."""

    if value is None:
        return ""
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            return ""
    text = str(value).strip()
    if text in {"", "NaT", "nan", "None"}:
        return ""
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    if len(text) == 8 and text.isdigit():
        return normalize_date_dash(text)
    return text


def dataframe_to_records(frame: Any) -> list[dict[str, Any]]:
    """Convert a pandas-like DataFrame to plain records."""

    return [dict(record) for record in frame.to_dict("records")]


def baostock_result_to_records(result: Any) -> list[dict[str, Any]]:
    """Convert a BaoStock query result to plain records."""

    rows: list[dict[str, Any]] = []
    while result.next():
        rows.append(dict(zip(result.fields, result.get_row_data())))
    return rows


def ensure_baostock_success(result: Any, action: str) -> None:
    """Raise a clear error when a BaoStock result signals failure."""

    if getattr(result, "error_code", "0") != "0":
        raise DataSourceError(f"{action} failed: {getattr(result, 'error_msg', '')}")


def _parse_date(value: str) -> datetime:
    stripped = value.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(stripped, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value}")
