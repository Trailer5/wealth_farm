"""Assemble fund and ETF research data from unified access."""

from __future__ import annotations

from src.data_access import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DATASET_FUND_ALLOCATION,
    DATASET_FUND_ESTIMATES,
    DATASET_FUND_SCALE,
    DATASET_FUND_STATUS,
    DATASET_OPEN_FUND_NAV,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
)
from src.data_src.models import (
    DisclosureDocument,
    DocumentTable,
    DocumentText,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_OPEN_FUND,
    MasterDataService,
)

from .models import (
    ETFResearchBundle,
    FundAssetRelation,
    FundRelationType,
    FundResearchBundle,
    OpenFundPeriodicSnapshot,
    OpenFundProfileSnapshot,
)
from .sqlite_repository import SQLiteFundResearchRepository


class FundResearchService:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        data_access: DataAccessService,
        repository: SQLiteFundResearchRepository,
    ) -> None:
        self.master_data = master_data
        self.data_access = data_access
        self.repository = repository
        self.repository.initialize()

    def add_manager_relation(
        self,
        *,
        fund_asset_id: str,
        manager_name: str,
        source: str,
        effective_date: str,
        confidence: float = 1.0,
    ) -> FundAssetRelation:
        self._require_fund(fund_asset_id)
        return self.repository.save_relation(
            FundAssetRelation(
                fund_asset_id=fund_asset_id,
                relation_type=FundRelationType.MANAGER,
                related_name=manager_name,
                source=source,
                effective_date=effective_date,
                confidence=confidence,
            )
        )

    def add_underlying_index_relation(
        self,
        *,
        fund_asset_id: str,
        index_asset_id: str,
        source: str,
        effective_date: str,
        confidence: float = 1.0,
    ) -> FundAssetRelation:
        self._require_fund(fund_asset_id)
        index = self.master_data.get_asset(index_asset_id)
        if index is None:
            raise KeyError(f"Unknown index asset_id: {index_asset_id}")
        if index.asset_type != ASSET_TYPE_INDEX:
            raise ValueError(
                "Underlying relation requires an index asset"
            )
        return self.repository.save_relation(
            FundAssetRelation(
                fund_asset_id=fund_asset_id,
                relation_type=FundRelationType.UNDERLYING_INDEX,
                related_asset_id=index.asset_id,
                related_name=(
                    index.display_name or index.standard_symbol
                ),
                source=source,
                effective_date=effective_date,
                confidence=confidence,
            )
        )

    def ingest_profile_disclosure(
        self,
        *,
        asset_id: str,
        document: DisclosureDocument,
        text: DocumentText,
        tables: list[DocumentTable],
        parser=None,
    ) -> OpenFundProfileSnapshot:
        """Parse and persist one official product-profile document."""

        self._require_fund(
            asset_id,
            expected_type=ASSET_TYPE_OPEN_FUND,
        )
        if parser is None:
            from src.document_processing.open_fund_disclosure import (
                OpenFundDisclosureParser,
            )

            parser = OpenFundDisclosureParser()
        snapshot = parser.parse_profile(
            fund_asset_id=asset_id,
            document=document,
            text=text,
            tables=tables,
        )
        return self.repository.save_profile_snapshot(snapshot)

    def ingest_periodic_disclosure(
        self,
        *,
        asset_id: str,
        document: DisclosureDocument,
        text: DocumentText,
        tables: list[DocumentTable],
        parser=None,
    ) -> OpenFundPeriodicSnapshot:
        """Parse and persist one official periodic-report document."""

        self._require_fund(
            asset_id,
            expected_type=ASSET_TYPE_OPEN_FUND,
        )
        if parser is None:
            from src.document_processing.open_fund_disclosure import (
                OpenFundDisclosureParser,
            )

            parser = OpenFundDisclosureParser()
        snapshot = parser.parse_periodic(
            fund_asset_id=asset_id,
            document=document,
            text=text,
            tables=tables,
        )
        return self.repository.save_periodic_snapshot(snapshot)

    def get_open_fund_profile_snapshot(
        self,
        *,
        asset_id: str,
        as_of_date: str,
    ) -> OpenFundProfileSnapshot | None:
        """Return the latest official profile through the service."""
        self._require_fund(
            asset_id,
            expected_type=ASSET_TYPE_OPEN_FUND,
        )
        return self.repository.get_latest_profile_snapshot(
            asset_id,
            as_of_date=as_of_date,
        )

    def get_open_fund_bundle(
        self,
        *,
        asset_id: str,
        start_date: str,
        end_date: str,
        as_of_date: str,
        fund_type: str = "股票型基金",
        allocation_year: str | None = None,
        allow_remote: bool = True,
        provider_timeout_seconds: float = 30.0,
    ) -> FundResearchBundle:
        asset = self._require_fund(
            asset_id,
            expected_type=ASSET_TYPE_OPEN_FUND,
        )
        responses = {}
        errors: dict[str, str] = {}
        for dataset in (
            DATASET_OPEN_FUND_NAV,
            DATASET_FUND_ESTIMATES,
            DATASET_FUND_STATUS,
            DATASET_FUND_SCALE,
            DATASET_FUND_ALLOCATION,
        ):
            parameters = {}
            if dataset == DATASET_FUND_SCALE:
                parameters["fund_type"] = fund_type
            if dataset == DATASET_FUND_ALLOCATION:
                parameters["year"] = (
                    allocation_year or end_date[:4]
                )
            try:
                responses[dataset] = self.data_access.get(
                    DataAccessRequest(
                        asset=AssetInput(asset_id=asset_id),
                        dataset=dataset,
                        start_date=start_date,
                        end_date=end_date,
                        as_of_date=as_of_date,
                        allow_remote=allow_remote,
                        allow_stale_on_error=True,
                        remote_sources=("akshare",),
                        parameters=parameters,
                        provider_timeout_seconds=(
                            provider_timeout_seconds
                        ),
                    )
                )
            except Exception as exc:
                errors[dataset] = f"{type(exc).__name__}: {exc}"
        relations = self.repository.list_relations(
            asset_id,
            as_of_date=as_of_date,
        )
        profile_snapshot = (
            self.repository.get_latest_profile_snapshot(
                asset_id,
                as_of_date=as_of_date,
            )
        )
        periodic_snapshot = (
            self.repository.get_latest_periodic_snapshot(
                asset_id,
                as_of_date=as_of_date,
            )
        )
        sources = {
            source
            for response in responses.values()
            for source in response.sources
        }
        if profile_snapshot is not None:
            sources.add(profile_snapshot.source)
        if periodic_snapshot is not None:
            sources.add(periodic_snapshot.source)
        return FundResearchBundle(
            asset=asset,
            start_date=start_date,
            end_date=end_date,
            as_of_date=as_of_date,
            nav_records=_records(
                responses.get(DATASET_OPEN_FUND_NAV)
            ),
            estimate_records=_records(
                responses.get(DATASET_FUND_ESTIMATES)
            ),
            status_records=_records(
                responses.get(DATASET_FUND_STATUS)
            ),
            scale_records=_records(
                responses.get(DATASET_FUND_SCALE)
            ),
            allocation_records=_records(
                responses.get(DATASET_FUND_ALLOCATION)
            ),
            relations=tuple(relations),
            sources=tuple(sorted(sources)),
            complete=(
                len(responses) == 5
                and all(
                    response.complete
                    for response in responses.values()
                )
                and not errors
            ),
            profile_snapshot=profile_snapshot,
            periodic_snapshot=periodic_snapshot,
            errors=errors,
        )

    def get_etf_bundle(
        self,
        *,
        asset_id: str,
        start_date: str,
        end_date: str,
        as_of_date: str,
        allow_remote: bool = True,
        provider_timeout_seconds: float = 30.0,
    ) -> ETFResearchBundle:
        asset = self._require_fund(
            asset_id,
            expected_type=ASSET_TYPE_EXCHANGE_FUND,
        )
        errors: dict[str, str] = {}
        response = None
        try:
            response = self.data_access.get(
                DataAccessRequest(
                    asset=AssetInput(asset_id=asset_id),
                    dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
                    start_date=start_date,
                    end_date=end_date,
                    as_of_date=as_of_date,
                    allow_remote=allow_remote,
                    allow_stale_on_error=True,
                    provider_timeout_seconds=(
                        provider_timeout_seconds
                    ),
                )
            )
        except Exception as exc:
            errors[DATASET_EXCHANGE_FUND_DAILY_BARS] = (
                f"{type(exc).__name__}: {exc}"
            )
        relations = self.repository.list_relations(
            asset_id,
            as_of_date=as_of_date,
        )
        return ETFResearchBundle(
            asset=asset,
            start_date=start_date,
            end_date=end_date,
            as_of_date=as_of_date,
            daily_bar_records=_records(response),
            relations=tuple(relations),
            sources=(
                response.sources
                if response is not None
                else ()
            ),
            complete=(
                response is not None
                and response.complete
                and not errors
            ),
            errors=errors,
        )

    def _require_fund(
        self,
        asset_id: str,
        expected_type: str | None = None,
    ):
        asset = self.master_data.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        allowed = {
            ASSET_TYPE_OPEN_FUND,
            ASSET_TYPE_EXCHANGE_FUND,
        }
        if asset.asset_type not in allowed:
            raise ValueError(
                f"Asset {asset_id} is not a fund product"
            )
        if (
            expected_type is not None
            and asset.asset_type != expected_type
        ):
            raise ValueError(
                f"Asset {asset_id} requires type {expected_type}"
            )
        return asset


def _records(response) -> tuple[dict[str, object], ...]:
    return response.records if response is not None else ()
