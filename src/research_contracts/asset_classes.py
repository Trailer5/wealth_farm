"""Portfolio asset classes shared by fund look-through and portfolio diagnosis."""

from __future__ import annotations

ASSET_CLASS_EQUITY = "equity"
ASSET_CLASS_BOND = "bond"
ASSET_CLASS_CASH = "cash"
ASSET_CLASS_COMMODITY = "gold_commodity"
ASSET_CLASS_OVERSEAS = "overseas"
ASSET_CLASS_OTHER = "other"
ASSET_CLASS_LABELS = {
    ASSET_CLASS_EQUITY: "权益",
    ASSET_CLASS_BOND: "债券",
    ASSET_CLASS_CASH: "现金类",
    ASSET_CLASS_COMMODITY: "黄金与商品",
    ASSET_CLASS_OVERSEAS: "跨境（QDII）",
    ASSET_CLASS_OTHER: "其他",
}
