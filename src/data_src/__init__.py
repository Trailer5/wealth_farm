"""External data source adapters.

Modules in this package talk to external data providers and expose normalized
interfaces to the rest of the project.
"""

from .akshare import AkShareDataSource
from .baostock import BaoStockDataSource
from .bse import BseDisclosureDataSource
from .cninfo import CninfoDataSource
from .csrc import CsrcPublicInfoDataSource
from .cs_fund import CsFundDisclosureDataSource
from .eastmoney import EastmoneyExchangeFundDataSource, EastmoneyResearchReportDataSource
from .fund_company import FundCompanyDisclosureDataSource
from .models import (
    DailyBar,
    DisclosureDocument,
    DocumentTable,
    DocumentText,
    DownloadedFile,
    FundEstimate,
    FundNav,
    SecurityInfo,
)
from .sina import SinaExchangeFundDataSource
from .sse import SseDisclosureDataSource
from .szse import SzseDisclosureDataSource
from .tencent import TencentExchangeFundDataSource

__all__ = [
    "AkShareDataSource",
    "BaoStockDataSource",
    "BseDisclosureDataSource",
    "CninfoDataSource",
    "CsrcPublicInfoDataSource",
    "CsFundDisclosureDataSource",
    "EastmoneyExchangeFundDataSource",
    "EastmoneyResearchReportDataSource",
    "FundCompanyDisclosureDataSource",
    "SinaExchangeFundDataSource",
    "SseDisclosureDataSource",
    "SzseDisclosureDataSource",
    "TencentExchangeFundDataSource",
    "DailyBar",
    "DisclosureDocument",
    "DocumentTable",
    "DocumentText",
    "DownloadedFile",
    "FundEstimate",
    "FundNav",
    "SecurityInfo",
]
