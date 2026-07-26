"""Unified identity-first access to local and remotely fetched data."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from src.data_src.common import infer_a_share_exchange, normalize_a_share_code
from src.data_src.service import DataFetchService
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
)

from .daily_data_reader import DailyDataReader
from .models import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DATASET_STOCK_DAILY_BARS,
    AssetInput,
    DataCoverage,
    DataAccessRequest,
    DataAccessResponse,
)
from .sqlite_repository import SQLiteDataAccessRepository
from .trading_calendar import TradingCalendarService


class AssetResolutionRequiredError(ValueError):
    """Raised when a data request does not resolve to one canonical asset."""


class DataAccessFetchError(RuntimeError):
    """Raised when missing data cannot be fetched successfully."""

    def __init__(
        self,
        message: str,
        provider_errors: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_errors = provider_errors or {}


class DataAccessService:
    """Resolve identity, inspect local coverage, and fetch missing data."""

    def __init__(
        self,
        master_data: MasterDataService,
        repository: SQLiteDataAccessRepository,
        fetch_service: DataFetchService,
        daily_data_reader: DailyDataReader | None = None,
        trading_calendar: TradingCalendarService | None = None,
    ) -> None:
        self.master_data = master_data
        self.repository = repository
        self.fetch_service = fetch_service
        self.daily_data_reader = daily_data_reader
        self.trading_calendar = trading_calendar
        self.repository.initialize()

    def get(self, request: DataAccessRequest) -> DataAccessResponse:
        """Return one supported dataset with provenance and coverage state."""

        self._validate_request(request)
        asset = self._resolve_asset(request.asset, request.dataset)
        request_metadata: dict[str, object] = {}
        if self.trading_calendar is not None:
            effective_end = self.trading_calendar.effective_end_date(
                request.start_date,
                request.end_date,
            )
            request_metadata["requested_end_date"] = request.end_date
            request_metadata["effective_end_date"] = effective_end
            request_metadata["trading_calendar"] = True
            if effective_end is None:
                return self._response(
                    asset=asset,
                    request=request,
                    records=[],
                    complete=True,
                    fetched_remote=False,
                    stale=False,
                    backends=(),
                    extra_metadata=request_metadata,
                )
            request = replace(
                request,
                end_date=effective_end,
                max_age_seconds=None,
            )
        remote_sources = self._remote_sources(request)
        preferred_source = remote_sources[0]
        sqlite_records = self.repository.get_records(
            asset,
            request.dataset,
            request.start_date,
            request.end_date,
        )
        sqlite_coverage = self.repository.find_fresh_coverage(
            asset_id=asset.asset_id,
            dataset=request.dataset,
            start_date=request.start_date,
            end_date=request.end_date,
            max_age_seconds=request.max_age_seconds,
        )
        daily_records: list[dict[str, object]] = []
        daily_coverage = None
        if self.daily_data_reader is not None:
            daily_records = self.daily_data_reader.get_records(
                asset,
                request.dataset,
                request.start_date,
                request.end_date,
            )
            daily_coverage = self.daily_data_reader.find_fresh_coverage(
                asset=asset,
                dataset=request.dataset,
                start_date=request.start_date,
                end_date=request.end_date,
                max_age_seconds=request.max_age_seconds,
            )
        coverage = _choose_coverage(sqlite_coverage, daily_coverage)
        records = _merge_daily_bars(
            daily_records=daily_records,
            sqlite_records=sqlite_records,
            preferred_source=preferred_source,
            prefer_sqlite=sqlite_coverage is not None or daily_coverage is None,
        )
        if coverage is not None:
            return self._response(
                asset=asset,
                request=request,
                records=records,
                complete=True,
                fetched_remote=False,
                stale=False,
                coverage_start=coverage.range_start,
                coverage_end=coverage.range_end,
                fallback_source=coverage.source,
                backends=_backends(
                    sqlite_records,
                    daily_records,
                    sqlite_coverage,
                    daily_coverage,
                ),
                extra_metadata=request_metadata,
            )

        if not request.allow_remote:
            return self._response(
                asset=asset,
                request=request,
                records=records,
                complete=False,
                fetched_remote=False,
                stale=bool(records),
                backends=_backends(
                    sqlite_records,
                    daily_records,
                    sqlite_coverage,
                    daily_coverage,
                ),
                extra_metadata=request_metadata,
            )

        try:
            fetch_result, successful_source, attempted_sources, provider_errors = (
                self._fetch_with_fallback(asset, request, remote_sources)
            )
            coverage = self.repository.mark_coverage(
                asset_id=asset.asset_id,
                dataset=request.dataset,
                start_date=request.start_date,
                end_date=request.end_date,
                source=successful_source,
            )
            sqlite_records = self.repository.get_records(
                asset,
                request.dataset,
                request.start_date,
                request.end_date,
            )
            records = _merge_daily_bars(
                daily_records=daily_records,
                sqlite_records=sqlite_records,
                preferred_source=successful_source,
                prefer_sqlite=True,
            )
            return self._response(
                asset=asset,
                request=request,
                records=records,
                complete=True,
                fetched_remote=True,
                stale=False,
                coverage_start=coverage.range_start,
                coverage_end=coverage.range_end,
                fallback_source=coverage.source,
                attempted_sources=attempted_sources,
                provider_errors=provider_errors,
                extra_metadata=request_metadata,
                backends=_backends(
                    sqlite_records,
                    daily_records,
                    coverage,
                    daily_coverage,
                ),
            )
        except Exception as exc:
            if request.allow_stale_on_error and records:
                return self._response(
                    asset=asset,
                    request=request,
                    records=records,
                    complete=False,
                    fetched_remote=False,
                    stale=True,
                    error=f"{type(exc).__name__}: {exc}",
                    attempted_sources=remote_sources,
                    provider_errors=(
                        exc.provider_errors
                        if isinstance(exc, DataAccessFetchError)
                        else {}
                    ),
                    extra_metadata=request_metadata,
                    backends=_backends(
                        sqlite_records,
                        daily_records,
                        sqlite_coverage,
                        daily_coverage,
                    ),
                )
            if isinstance(exc, DataAccessFetchError):
                raise
            raise DataAccessFetchError(
                f"Failed to fetch {request.dataset} for {asset.asset_id}: {exc}",
                provider_errors=(
                    exc.provider_errors
                    if isinstance(exc, DataAccessFetchError)
                    else {}
                ),
            ) from exc

    def _resolve_asset(self, asset_input: AssetInput, dataset: str) -> Asset:
        if asset_input.asset_id is not None:
            asset = self.master_data.get_asset(asset_input.asset_id)
            if asset is None:
                raise AssetResolutionRequiredError(
                    f"Unknown asset_id: {asset_input.asset_id}"
                )
            return asset
        if not asset_input.raw_symbol:
            raise AssetResolutionRequiredError(
                "Data request requires asset_id or raw_symbol"
            )
        expected_asset_type = {
            DATASET_STOCK_DAILY_BARS: ASSET_TYPE_STOCK,
            DATASET_EXCHANGE_FUND_DAILY_BARS: ASSET_TYPE_EXCHANGE_FUND,
        }.get(dataset)
        if expected_asset_type is not None:
            try:
                standard_symbol = normalize_a_share_code(asset_input.raw_symbol)
            except ValueError as exc:
                raise AssetResolutionRequiredError(str(exc)) from exc
            canonical_matches = self.master_data.find_assets(
                standard_symbol=standard_symbol,
                asset_type=expected_asset_type,
            )
            if len(canonical_matches) == 1:
                return canonical_matches[0]
            if not canonical_matches and self.daily_data_reader is not None:
                exchange = infer_a_share_exchange(standard_symbol)
                daily_asset = self.daily_data_reader.get_asset(
                    market=asset_input.market_hint or "cn_a",
                    exchange=exchange,
                    standard_symbol=standard_symbol,
                )
                if daily_asset is not None:
                    return self.master_data.register_asset(
                        daily_asset,
                        source="daily_data:asset_catalog",
                        raw_symbol=standard_symbol,
                    )
        asset_type_hint = asset_input.asset_type_hint
        if asset_type_hint is None and dataset == DATASET_STOCK_DAILY_BARS:
            asset_type_hint = "股票"
        if asset_type_hint is None and dataset == DATASET_EXCHANGE_FUND_DAILY_BARS:
            asset_type_hint = "ETF"
        resolution = self.master_data.resolve_identifier(
            source=asset_input.source,
            raw_symbol=asset_input.raw_symbol,
            name=asset_input.name,
            asset_type_hint=asset_type_hint,
            market_hint=asset_input.market_hint,
            exchange_hint=asset_input.exchange_hint,
            create=True,
        )
        if resolution.asset is None or resolution.needs_review:
            raise AssetResolutionRequiredError(
                f"Asset identity requires review: {resolution.reason}"
            )
        return resolution.asset

    def _fetch_with_fallback(
        self,
        asset: Asset,
        request: DataAccessRequest,
        remote_sources: tuple[str, ...],
    ):
        provider_errors: dict[str, str] = {}
        attempted_sources: list[str] = []
        for source in remote_sources:
            attempted_sources.append(source)
            try:
                result = self._fetch_remote(asset, request, source)
                if result.fetched_count <= 0:
                    raise DataAccessFetchError(
                        f"Remote source {source} returned no records"
                    )
                return result, source, tuple(attempted_sources), provider_errors
            except Exception as exc:
                provider_errors[source] = f"{type(exc).__name__}: {exc}"
        raise DataAccessFetchError(
            f"All remote sources failed for {request.dataset}: "
            f"{', '.join(attempted_sources)}",
            provider_errors=provider_errors,
        )

    def _fetch_remote(
        self,
        asset: Asset,
        request: DataAccessRequest,
        source: str,
    ):
        if request.dataset == DATASET_STOCK_DAILY_BARS:
            return self.fetch_service.fetch_and_store_a_share_daily_bars(
                symbol=asset.standard_symbol,
                start_date=request.start_date,
                end_date=request.end_date,
                source=source,
                timeout_seconds=request.provider_timeout_seconds,
            )
        if request.dataset == DATASET_EXCHANGE_FUND_DAILY_BARS:
            return self.fetch_service.fetch_and_store_etf_daily_bars(
                symbol=asset.standard_symbol,
                start_date=request.start_date,
                end_date=request.end_date,
                source=source,
                timeout_seconds=request.provider_timeout_seconds,
            )
        raise DataAccessFetchError(f"Unsupported remote dataset: {request.dataset}")

    def _response(
        self,
        *,
        asset: Asset,
        request: DataAccessRequest,
        records: list[dict[str, object]],
        complete: bool,
        fetched_remote: bool,
        stale: bool,
        coverage_start: str | None = None,
        coverage_end: str | None = None,
        fallback_source: str | None = None,
        error: str | None = None,
        backends: tuple[str, ...] = ("sqlite",),
        attempted_sources: tuple[str, ...] = (),
        provider_errors: dict[str, str] | None = None,
        extra_metadata: dict[str, object] | None = None,
    ) -> DataAccessResponse:
        sources = sorted(
            {
                str(record["source"])
                for record in records
                if record.get("source") is not None
            }
        )
        if not sources and fallback_source is not None:
            sources.append(fallback_source)
        metadata: dict[str, object] = {
            "backends": list(backends),
            "attempted_sources": list(attempted_sources),
            "provider_errors": provider_errors or {},
        }
        metadata.update(extra_metadata or {})
        return DataAccessResponse(
            asset=asset,
            dataset=request.dataset,
            records=tuple(records),
            sources=tuple(sources),
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            complete=complete,
            fetched_remote=fetched_remote,
            stale=stale,
            error=error,
            metadata=metadata,
        )

    def _validate_request(self, request: DataAccessRequest) -> None:
        if request.dataset not in {
            DATASET_STOCK_DAILY_BARS,
            DATASET_EXCHANGE_FUND_DAILY_BARS,
        }:
            raise ValueError(f"Unsupported dataset: {request.dataset}")
        start = date.fromisoformat(request.start_date)
        end = date.fromisoformat(request.end_date)
        if start > end:
            raise ValueError("start_date must not be after end_date")
        if request.max_age_seconds is not None and request.max_age_seconds < 0:
            raise ValueError("max_age_seconds must not be negative")
        if request.provider_timeout_seconds <= 0:
            raise ValueError("provider_timeout_seconds must be positive")

    def _remote_sources(self, request: DataAccessRequest) -> tuple[str, ...]:
        if request.remote_sources:
            return request.remote_sources
        if request.dataset == DATASET_EXCHANGE_FUND_DAILY_BARS:
            return ("mootdx", "tencent", "sina", "baostock", "akshare")
        return ("mootdx", "tencent", "sina", "baostock", "akshare")


def _choose_coverage(
    sqlite_coverage: DataCoverage | None,
    daily_coverage: DataCoverage | None,
) -> DataCoverage | None:
    candidates = [
        coverage
        for coverage in (sqlite_coverage, daily_coverage)
        if coverage is not None
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda coverage: coverage.checked_at, reverse=True)
    return candidates[0]


def _merge_daily_bars(
    *,
    daily_records: list[dict[str, object]],
    sqlite_records: list[dict[str, object]],
    preferred_source: str,
    prefer_sqlite: bool,
) -> list[dict[str, object]]:
    selected: dict[str, tuple[tuple[int, int, str], dict[str, object]]] = {}
    batches = (
        (daily_records, 0 if prefer_sqlite else 10),
        (sqlite_records, 10 if prefer_sqlite else 0),
    )
    for records, backend_score in batches:
        for record in records:
            trade_date = str(record.get("trade_date", ""))
            source = str(record.get("source", ""))
            source_score = 1 if (
                source == preferred_source or source.startswith(f"{preferred_source}:")
            ) else 0
            score = (backend_score, source_score, source)
            current = selected.get(trade_date)
            if current is None or score > current[0]:
                selected[trade_date] = (score, record)
    return [selected[key][1] for key in sorted(selected)]


def _backends(
    sqlite_records: list[dict[str, object]],
    daily_records: list[dict[str, object]],
    sqlite_coverage: DataCoverage | None,
    daily_coverage: DataCoverage | None,
) -> tuple[str, ...]:
    backends: list[str] = []
    if daily_records or (
        daily_coverage is not None and daily_coverage.backend == "daily_data"
    ):
        backends.append("daily_data")
    if sqlite_records or (
        sqlite_coverage is not None and sqlite_coverage.backend == "sqlite"
    ):
        backends.append("sqlite")
    return tuple(backends)
