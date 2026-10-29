"""External data source adapters.

Modules in this package talk to external data providers and expose normalized
interfaces to the rest of the project. Importing the package routes every
HTTP call (requests and urllib) through the shared source throttle (see throttle.py), so
provider child processes and libraries such as AkShare are covered too.
"""

from .throttle import install_http_throttle

install_http_throttle()

from .akshare import AkShareDataSource  # noqa: E402
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
    IndexConstituent,
    SecurityInfo,
    StockIndustryInfo,
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
    "IndexConstituent",
    "SecurityInfo",
    "StockIndustryInfo",
]
