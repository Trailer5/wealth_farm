"""Unified identity-first access to local and remotely fetched data."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

from src.data_src.common import (
    infer_a_share_exchange,
    normalize_a_share_code,
    to_date_string,
)
from src.data_src.service import DataFetchService
from src.financial_data import (
    FINANCIAL_DATASETS,
    FinancialResearchBundle,
    SQLiteFinancialDataRepository,
    normalize_financial_records,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
)

from .daily_data_reader import DailyDataReader
from .models import (
    DATASET_DISCLOSURES,
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DATASET_FUND_ALLOCATION,
    DATASET_FUND_ESTIMATES,
    DATASET_FUND_SCALE,
    DATASET_FUND_STATUS,
    DATASET_INDEX_DAILY_BARS,
    DATASET_OPEN_FUND_NAV,
    DATASET_RESEARCH_REPORTS,
    DATASET_STOCK_DAILY_BARS,
    AssetInput,
    DataCoverage,
    DataAccessRequest,
    DataAccessResponse,
    FUND_DATASETS,
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
        financial_repository: (
            SQLiteFinancialDataRepository | None
        ) = None,
    ) -> None:
        self.master_data = master_data
        self.repository = repository
        self.fetch_service = fetch_service
        self.daily_data_reader = daily_data_reader
        self.trading_calendar = trading_calendar
        self.financial_repository = (
            financial_repository
            or SQLiteFinancialDataRepository(
                repository.store.db_path
            )
        )
        self.repository.initialize()
        self.financial_repository.initialize()

    def get(self, request: DataAccessRequest) -> DataAccessResponse:
        """Return one supported dataset with provenance and coverage state."""

        self._validate_request(request)
        if request.dataset in FINANCIAL_DATASETS:
            return self._get_financial_data(request)
        if request.dataset in {
            DATASET_DISCLOSURES,
            DATASET_RESEARCH_REPORTS,
        }:
            return self._get_research_metadata(request)
        if request.dataset in FUND_DATASETS:
            return self._get_fund_data(request)
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
        if (
            self.daily_data_reader is not None
            and request.dataset
            in {
                DATASET_STOCK_DAILY_BARS,
                DATASET_EXCHANGE_FUND_DAILY_BARS,
            }
        ):
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

    def _get_fund_data(
        self,
        request: DataAccessRequest,
    ) -> DataAccessResponse:
        asset = self._resolve_asset(request.asset, request.dataset)
        as_of_date = request.as_of_date or request.end_date
        effective_end = min(request.end_date, as_of_date)
        if effective_end < request.start_date:
            return self._response(
                asset=asset,
                request=request,
                records=[],
                complete=True,
                fetched_remote=False,
                stale=False,
                extra_metadata={"as_of_date": as_of_date},
                backends=(),
            )
        records = self._local_fund_data(
            asset,
            request,
            effective_end,
        )
        coverage = self.repository.find_fresh_coverage(
            asset_id=asset.asset_id,
            dataset=request.dataset,
            start_date=request.start_date,
            end_date=effective_end,
            max_age_seconds=request.max_age_seconds,
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
                extra_metadata={"as_of_date": as_of_date},
                backends=("sqlite",) if records else (),
            )
        if not request.allow_remote:
            return self._response(
                asset=asset,
                request=request,
                records=records,
                complete=False,
                fetched_remote=False,
                stale=bool(records),
                extra_metadata={"as_of_date": as_of_date},
                backends=("sqlite",) if records else (),
            )

        source = (
            request.remote_sources[0]
            if request.remote_sources
            else "akshare"
        )
        try:
            fetched_count = self._fetch_and_store_fund_data(
                asset,
                request,
                source,
                effective_end,
            )
            coverage = self.repository.mark_coverage(
                asset_id=asset.asset_id,
                dataset=request.dataset,
                start_date=request.start_date,
                end_date=effective_end,
                source=source,
            )
            records = self._local_fund_data(
                asset,
                request,
                effective_end,
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
                fallback_source=source,
                extra_metadata={
                    "as_of_date": as_of_date,
                    "attempted_sources": [source],
                    "provider_errors": {},
                    "fetched_record_count": fetched_count,
                },
                backends=("sqlite",) if records else (),
            )
        except Exception as exc:
            provider_errors = {
                source: f"{type(exc).__name__}: {exc}"
            }
            if request.allow_stale_on_error and records:
                return self._response(
                    asset=asset,
                    request=request,
                    records=records,
                    complete=False,
                    fetched_remote=False,
                    stale=True,
                    error="Fund data source failed",
                    extra_metadata={
                        "as_of_date": as_of_date,
                        "attempted_sources": [source],
                        "provider_errors": provider_errors,
                    },
                    backends=("sqlite",),
                )
            raise DataAccessFetchError(
                f"Failed to fetch {request.dataset} for "
                f"{asset.asset_id}: {exc}",
                provider_errors=provider_errors,
            ) from exc

    def _fetch_and_store_fund_data(
        self,
        asset: Asset,
        request: DataAccessRequest,
        source: str,
        effective_end: str,
    ) -> int:
        timeout = request.provider_timeout_seconds
        if request.dataset == DATASET_OPEN_FUND_NAV:
            rows = self.fetch_service.fetch_open_fund_nav_history(
                asset.standard_symbol,
                source=source,
                timeout_seconds=timeout,
            )
            return self.repository.store.upsert_fund_nav_daily(rows)
        if request.dataset == DATASET_FUND_ESTIMATES:
            rows = self.fetch_service.fetch_fund_value_estimates(
                source=source,
                timeout_seconds=timeout,
            )
            return self.repository.store.upsert_fund_estimates(rows)
        if request.dataset == DATASET_FUND_STATUS:
            rows = self.fetch_service.fetch_fund_purchase_status(
                source=source,
                timeout_seconds=timeout,
            )
            return self.repository.store.upsert_fund_purchase_status(
                rows
            )
        if request.dataset == DATASET_FUND_SCALE:
            rows = self.fetch_service.fetch_fund_scale_records(
                fund_type=str(
                    request.parameters.get(
                        "fund_type",
                        "股票型基金",
                    )
                ),
                source=source,
                timeout_seconds=timeout,
            )
            return self.repository.store.upsert_fund_report_records(
                rows
            )
        if request.dataset == DATASET_FUND_ALLOCATION:
            rows = (
                self.fetch_service.fetch_fund_allocation_records(
                    asset.standard_symbol,
                    year=str(
                        request.parameters.get(
                            "year",
                            effective_end[:4],
                        )
                    ),
                    source=source,
                    timeout_seconds=timeout,
                )
            )
            return self.repository.store.upsert_fund_report_records(
                rows
            )
        raise ValueError(
            f"Unsupported fund dataset: {request.dataset}"
        )

    def _local_fund_data(
        self,
        asset: Asset,
        request: DataAccessRequest,
        effective_end: str,
    ) -> list[dict[str, object]]:
        store = self.repository.store
        if request.dataset == DATASET_OPEN_FUND_NAV:
            rows = store.get_fund_nav_daily(
                asset.standard_symbol,
                start_date=request.start_date,
                end_date=effective_end,
                estimated=False,
            )
        elif request.dataset == DATASET_FUND_ESTIMATES:
            rows = store.get_fund_estimates(
                asset.standard_symbol,
                start_date=request.start_date,
                end_date=effective_end,
            )
        elif request.dataset == DATASET_FUND_STATUS:
            rows = store.get_fund_purchase_status(
                asset.standard_symbol,
                as_of_date=effective_end,
            )
        elif request.dataset == DATASET_FUND_SCALE:
            rows = store.get_fund_report_records(
                asset.standard_symbol,
                report_type="fund_scale_open_sina",
                start_date=request.start_date,
                end_date=effective_end,
            )
        elif request.dataset == DATASET_FUND_ALLOCATION:
            rows = [
                row
                for row in store.get_fund_report_records(
                    asset.standard_symbol,
                    start_date=request.start_date,
                    end_date=effective_end,
                )
                if "allocation" in str(row["report_type"])
            ]
        else:
            raise ValueError(
                f"Unsupported fund dataset: {request.dataset}"
            )
        return [_decode_raw_json(row) for row in rows]

    def _get_research_metadata(
        self,
        request: DataAccessRequest,
    ) -> DataAccessResponse:
        asset = self._resolve_asset(request.asset, request.dataset)
        as_of_date = request.as_of_date or request.end_date
        effective_end = min(request.end_date, as_of_date)
        if effective_end < request.start_date:
            return self._response(
                asset=asset,
                request=request,
                records=[],
                complete=True,
                fetched_remote=False,
                stale=False,
                extra_metadata={"as_of_date": as_of_date},
                backends=(),
            )
        records = self._local_research_metadata(
            asset,
            request.dataset,
            request.start_date,
            effective_end,
        )
        coverage = self.repository.find_fresh_coverage(
            asset_id=asset.asset_id,
            dataset=request.dataset,
            start_date=request.start_date,
            end_date=effective_end,
            max_age_seconds=request.max_age_seconds,
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
                extra_metadata={"as_of_date": as_of_date},
                backends=("sqlite",) if records else (),
            )
        if not request.allow_remote:
            return self._response(
                asset=asset,
                request=request,
                records=records,
                complete=False,
                fetched_remote=False,
                stale=bool(records),
                extra_metadata={"as_of_date": as_of_date},
                backends=("sqlite",) if records else (),
            )

        attempted_sources: list[str] = []
        provider_errors: dict[str, str] = {}
        empty_sources: list[str] = []
        sources = self._research_metadata_sources(asset, request)
        for source in sources:
            attempted_sources.append(source)
            try:
                if request.dataset == DATASET_DISCLOSURES:
                    fetched = (
                        self.fetch_service
                        .fetch_disclosure_documents_by_source(
                            asset.standard_symbol,
                            request.start_date,
                            effective_end,
                            source,
                            timeout_seconds=(
                                request.provider_timeout_seconds
                            ),
                        )
                    )
                    if not fetched:
                        empty_sources.append(source)
                        continue
                    self.repository.store.upsert_disclosure_documents(
                        fetched
                    )
                else:
                    fetched = (
                        self.fetch_service
                        .fetch_stock_research_reports(
                            asset.standard_symbol,
                            request.start_date,
                            effective_end,
                            source=source,
                            timeout_seconds=(
                                request.provider_timeout_seconds
                            ),
                        )
                    )
                    if not fetched:
                        empty_sources.append(source)
                        continue
                    self.repository.store.upsert_research_reports(
                        fetched
                    )
                coverage = self.repository.mark_coverage(
                    asset_id=asset.asset_id,
                    dataset=request.dataset,
                    start_date=request.start_date,
                    end_date=effective_end,
                    source=source,
                )
                records = self._local_research_metadata(
                    asset,
                    request.dataset,
                    request.start_date,
                    effective_end,
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
                    fallback_source=source,
                    extra_metadata={
                        "as_of_date": as_of_date,
                        "attempted_sources": attempted_sources,
                        "provider_errors": provider_errors,
                    },
                    backends=("sqlite",),
                )
            except Exception as exc:
                provider_errors[source] = (
                    f"{type(exc).__name__}: {exc}"
                )
        if empty_sources:
            source = ",".join(empty_sources)
            coverage = self.repository.mark_coverage(
                asset_id=asset.asset_id,
                dataset=request.dataset,
                start_date=request.start_date,
                end_date=effective_end,
                source=source,
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
                fallback_source=source,
                extra_metadata={
                    "as_of_date": as_of_date,
                    "attempted_sources": attempted_sources,
                    "provider_errors": provider_errors,
                    "empty_sources": empty_sources,
                },
                backends=("sqlite",) if records else (),
            )
        if request.allow_stale_on_error and records:
            return self._response(
                asset=asset,
                request=request,
                records=records,
                complete=False,
                fetched_remote=False,
                stale=True,
                error="All research metadata sources failed",
                extra_metadata={
                    "as_of_date": as_of_date,
                    "attempted_sources": attempted_sources,
                    "provider_errors": provider_errors,
                },
                backends=("sqlite",),
            )
        raise DataAccessFetchError(
            f"All remote sources failed for {request.dataset}: "
            f"{', '.join(attempted_sources)}",
            provider_errors=provider_errors,
        )

    def _local_research_metadata(
        self,
        asset: Asset,
        dataset: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, object]]:
        if dataset == DATASET_DISCLOSURES:
            rows = self.repository.store.get_disclosure_documents(
                asset.standard_symbol,
                start_date=start_date,
                end_date=end_date,
            )
        elif dataset == DATASET_RESEARCH_REPORTS:
            rows = self.repository.store.get_research_reports(
                asset.standard_symbol,
                start_date=start_date,
                end_date=end_date,
            )
        else:
            raise ValueError(
                f"Unsupported research metadata dataset: {dataset}"
            )
        return [_decode_raw_json(row) for row in rows]

    def _research_metadata_sources(
        self,
        asset: Asset,
        request: DataAccessRequest,
    ) -> tuple[str, ...]:
        if request.remote_sources:
            return request.remote_sources
        if request.dataset == DATASET_RESEARCH_REPORTS:
            return ("eastmoney",)
        if asset.asset_type == ASSET_TYPE_OPEN_FUND:
            return ("cs_fund_disclosure",)
        by_exchange = {
            "sh": ("sse", "cninfo"),
            "sz": ("szse", "cninfo"),
            "bj": ("bse", "cninfo"),
        }
        return by_exchange.get(
            str(asset.exchange or "").lower(),
            ("cninfo",),
        )

    def get_financial_bundle(
        self,
        *,
        asset_id: str,
        start_date: str,
        end_date: str,
        as_of_date: str,
        allow_remote: bool = True,
        allow_stale_on_error: bool = False,
        provider_timeout_seconds: float = 30.0,
    ) -> FinancialResearchBundle:
        asset = self.master_data.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        records_by_dataset: dict[
            str,
            tuple[dict[str, object], ...],
        ] = {}
        sources: set[str] = set()
        errors: dict[str, str] = {}
        complete = True
        for dataset in sorted(FINANCIAL_DATASETS):
            try:
                response = self.get(
                    DataAccessRequest(
                        asset=AssetInput(asset_id=asset_id),
                        dataset=dataset,
                        start_date=start_date,
                        end_date=end_date,
                        as_of_date=as_of_date,
                        max_age_seconds=86400,
                        allow_remote=allow_remote,
                        allow_stale_on_error=(
                            allow_stale_on_error
                        ),
                        remote_sources=("akshare",),
                        provider_timeout_seconds=(
                            provider_timeout_seconds
                        ),
                    )
                )
                records_by_dataset[dataset] = response.records
                sources.update(response.sources)
                complete = complete and response.complete
                if response.error:
                    errors[dataset] = response.error
            except Exception as exc:
                records_by_dataset[dataset] = ()
                errors[dataset] = f"{type(exc).__name__}: {exc}"
                complete = False
        return FinancialResearchBundle(
            asset=asset,
            as_of_date=as_of_date,
            start_date=start_date,
            end_date=end_date,
            records_by_dataset=records_by_dataset,
            sources=tuple(sorted(sources)),
            complete=complete and not errors,
            errors=errors,
        )

    def _get_financial_data(
        self,
        request: DataAccessRequest,
    ) -> DataAccessResponse:
        asset = self._resolve_asset(request.asset, request.dataset)
        as_of_date = request.as_of_date or request.end_date
        points = self.financial_repository.list_points(
            asset_id=asset.asset_id,
            dataset=request.dataset,
            start_date=request.start_date,
            end_date=request.end_date,
            as_of_date=as_of_date,
        )
        records = [point.to_record() for point in points]
        coverage = self.repository.find_fresh_coverage(
            asset_id=asset.asset_id,
            dataset=request.dataset,
            start_date=request.start_date,
            end_date=request.end_date,
            max_age_seconds=request.max_age_seconds,
        )
        metadata = {
            "as_of_date": as_of_date,
            "backends": ["sqlite"] if records else [],
            "attempted_sources": [],
            "provider_errors": {},
        }
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
                extra_metadata=metadata,
                backends=("sqlite",) if records else (),
            )
        if not request.allow_remote:
            return self._response(
                asset=asset,
                request=request,
                records=records,
                complete=False,
                fetched_remote=False,
                stale=bool(records),
                extra_metadata=metadata,
                backends=("sqlite",) if records else (),
            )

        provider_errors: dict[str, str] = {}
        attempted_sources: list[str] = []
        for source in self._remote_sources(request):
            attempted_sources.append(source)
            try:
                raw_records = (
                    self.fetch_service.fetch_stock_financial_records(
                        asset.standard_symbol,
                        request.dataset,
                        source=source,
                        timeout_seconds=(
                            request.provider_timeout_seconds
                        ),
                    )
                )
                if not raw_records:
                    raise DataAccessFetchError(
                        f"Remote source {source} returned no records"
                    )
                raw_records = _financial_records_in_range(
                    raw_records,
                    request.start_date,
                    request.end_date,
                )
                if not raw_records:
                    raise DataAccessFetchError(
                        "Remote financial records did not cover "
                        "the requested period"
                    )
                normalized = normalize_financial_records(
                    asset_id=asset.asset_id,
                    dataset=request.dataset,
                    records=raw_records,
                    source=source,
                )
                self.financial_repository.save_points(normalized)
                coverage = self.repository.mark_coverage(
                    asset_id=asset.asset_id,
                    dataset=request.dataset,
                    start_date=request.start_date,
                    end_date=request.end_date,
                    source=source,
                )
                points = self.financial_repository.list_points(
                    asset_id=asset.asset_id,
                    dataset=request.dataset,
                    start_date=request.start_date,
                    end_date=request.end_date,
                    as_of_date=as_of_date,
                )
                metadata.update(
                    {
                        "backends": ["sqlite"],
                        "attempted_sources": attempted_sources,
                        "provider_errors": provider_errors,
                    }
                )
                return self._response(
                    asset=asset,
                    request=request,
                    records=[
                        point.to_record()
                        for point in points
                    ],
                    complete=True,
                    fetched_remote=True,
                    stale=False,
                    coverage_start=coverage.range_start,
                    coverage_end=coverage.range_end,
                    fallback_source=source,
                    extra_metadata=metadata,
                    backends=("sqlite",),
                )
            except Exception as exc:
                provider_errors[source] = (
                    f"{type(exc).__name__}: {exc}"
                )
        if request.allow_stale_on_error and records:
            metadata.update(
                {
                    "backends": ["sqlite"],
                    "attempted_sources": attempted_sources,
                    "provider_errors": provider_errors,
                }
            )
            return self._response(
                asset=asset,
                request=request,
                records=records,
                complete=False,
                fetched_remote=False,
                stale=True,
                error="All financial data sources failed",
                extra_metadata=metadata,
                backends=("sqlite",),
            )
        raise DataAccessFetchError(
            f"All remote sources failed for {request.dataset}: "
            f"{', '.join(attempted_sources)}",
            provider_errors=provider_errors,
        )

    def _resolve_asset(self, asset_input: AssetInput, dataset: str) -> Asset:
        expected_asset_type = {
            DATASET_STOCK_DAILY_BARS: ASSET_TYPE_STOCK,
            DATASET_EXCHANGE_FUND_DAILY_BARS: ASSET_TYPE_EXCHANGE_FUND,
            DATASET_INDEX_DAILY_BARS: ASSET_TYPE_INDEX,
            DATASET_RESEARCH_REPORTS: ASSET_TYPE_STOCK,
            **{
                item: ASSET_TYPE_OPEN_FUND
                for item in FUND_DATASETS
            },
            **{
                item: ASSET_TYPE_STOCK
                for item in FINANCIAL_DATASETS
            },
        }.get(dataset)
        if asset_input.asset_id is not None:
            asset = self.master_data.get_asset(asset_input.asset_id)
            if asset is None:
                raise AssetResolutionRequiredError(
                    f"Unknown asset_id: {asset_input.asset_id}"
                )
            if (
                expected_asset_type is not None
                and asset.asset_type != expected_asset_type
            ):
                raise AssetResolutionRequiredError(
                    f"Dataset {dataset} requires asset_type "
                    f"{expected_asset_type}, got {asset.asset_type}"
                )
            if (
                dataset == DATASET_DISCLOSURES
                and asset.asset_type
                not in {ASSET_TYPE_STOCK, ASSET_TYPE_OPEN_FUND}
            ):
                raise AssetResolutionRequiredError(
                    "Dataset disclosures requires a stock or "
                    "open_fund asset"
                )
            return asset
        if not asset_input.raw_symbol:
            raise AssetResolutionRequiredError(
                "Data request requires asset_id or raw_symbol"
            )
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
            if (
                not canonical_matches
                and self.daily_data_reader is not None
                and expected_asset_type
                in {
                    ASSET_TYPE_STOCK,
                    ASSET_TYPE_EXCHANGE_FUND,
                }
            ):
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
        if asset_type_hint is None and (
            dataset == DATASET_STOCK_DAILY_BARS
            or dataset in FINANCIAL_DATASETS
            or dataset == DATASET_RESEARCH_REPORTS
        ):
            asset_type_hint = "股票"
        if asset_type_hint is None and dataset == DATASET_EXCHANGE_FUND_DAILY_BARS:
            asset_type_hint = "ETF"
        if asset_type_hint is None and dataset == DATASET_INDEX_DAILY_BARS:
            asset_type_hint = "指数"
        if asset_type_hint is None and dataset in FUND_DATASETS:
            asset_type_hint = "基金"
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
        if request.dataset == DATASET_INDEX_DAILY_BARS:
            if not asset.exchange:
                raise DataAccessFetchError(
                    "Index daily bars require an explicit exchange"
                )
            return self.fetch_service.fetch_and_store_index_daily_bars(
                symbol=asset.standard_symbol,
                start_date=request.start_date,
                end_date=request.end_date,
                exchange=str(asset.exchange or ""),
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
            DATASET_INDEX_DAILY_BARS,
            DATASET_DISCLOSURES,
            DATASET_RESEARCH_REPORTS,
            *FINANCIAL_DATASETS,
            *FUND_DATASETS,
        }:
            raise ValueError(f"Unsupported dataset: {request.dataset}")
        start = date.fromisoformat(request.start_date)
        end = date.fromisoformat(request.end_date)
        if start > end:
            raise ValueError("start_date must not be after end_date")
        if request.as_of_date is not None:
            date.fromisoformat(request.as_of_date)
        if (
            request.dataset in FINANCIAL_DATASETS
            and request.as_of_date is None
        ):
            raise ValueError(
                "Financial data requests require as_of_date"
            )
        if request.max_age_seconds is not None and request.max_age_seconds < 0:
            raise ValueError("max_age_seconds must not be negative")
        if request.provider_timeout_seconds <= 0:
            raise ValueError("provider_timeout_seconds must be positive")

    def _remote_sources(self, request: DataAccessRequest) -> tuple[str, ...]:
        if request.remote_sources:
            return request.remote_sources
        if request.dataset in FINANCIAL_DATASETS:
            return ("akshare",)
        if request.dataset == DATASET_INDEX_DAILY_BARS:
            return (
                "csindex",
                "tencent",
                "sina",
                "eastmoney",
                "akshare",
            )
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


def _financial_records_in_range(
    records: list[dict[str, object]],
    start_date: str,
    end_date: str,
) -> list[dict[str, object]]:
    keys = (
        "REPORT_DATE",
        "报告期",
        "报告日期",
        "截止日期",
        "date",
    )
    filtered = []
    for record in records:
        report_date = ""
        for key in keys:
            value = to_date_string(record.get(key))
            if value:
                report_date = value
                break
        if start_date <= report_date <= end_date:
            filtered.append(record)
    return filtered


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


def _decode_raw_json(
    row: dict[str, object],
) -> dict[str, object]:
    result = dict(row)
    raw_json = result.pop("raw_json", None)
    if raw_json:
        try:
            result["raw"] = json.loads(str(raw_json))
        except json.JSONDecodeError:
            result["raw"] = {"raw_json": str(raw_json)}
    else:
        result["raw"] = {}
    return result
