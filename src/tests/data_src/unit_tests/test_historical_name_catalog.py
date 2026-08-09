import unittest

from src.data_src.akshare import AkShareDataSource
from src.event_name_discovery import build_shenzhen_name_snapshots


class FakeFrame:
    def __init__(self, records):
        self.records = records

    def to_dict(self, orient):
        if orient != "records":
            raise AssertionError("records orientation required")
        return self.records


class FakeAkShareHistoricalNames:
    def stock_info_sz_name_code(self, **kwargs):
        self._expect(kwargs, {"symbol": "A股列表"})
        return FakeFrame(
            [
                {
                    "A股代码": "000001",
                    "A股简称": "当前名称",
                    "A股上市日期": "2020-01-01",
                }
            ]
        )

    def stock_info_sz_delist(self, **kwargs):
        self._expect(kwargs, {"symbol": "终止上市公司"})
        return FakeFrame(
            [
                {
                    "证券代码": "000002",
                    "证券简称": "退市名称",
                    "上市日期": "2019-01-01",
                    "终止上市日期": "2022-12-31",
                }
            ]
        )

    def stock_info_sz_change_name(self, **kwargs):
        self._expect(kwargs, {"symbol": "简称变更"})
        return FakeFrame(
            [
                {
                    "变更日期": "2021-01-01",
                    "证券代码": "000001",
                    "证券简称": "当前名称",
                    "变更前简称": "历史名称",
                    "变更后简称": "当前名称",
                }
            ]
        )

    @staticmethod
    def _expect(actual, expected):
        if actual != expected:
            raise AssertionError((actual, expected))


class HistoricalNameCatalogTest(unittest.TestCase):
    def test_provider_and_builder_preserve_delisted_and_old_names(self):
        provider = AkShareDataSource(FakeAkShareHistoricalNames())
        catalog = provider.get_shenzhen_a_share_catalog()
        changes = provider.get_shenzhen_name_change_history()

        old = build_shenzhen_name_snapshots(
            catalog=catalog,
            name_changes=changes,
            event_date="2020-06-01",
        )
        after_delist = build_shenzhen_name_snapshots(
            catalog=catalog,
            name_changes=changes,
            event_date="2023-01-01",
        )

        by_symbol = {item.standard_symbol: item for item in old}
        self.assertEqual(by_symbol["000001"].name, "历史名称")
        self.assertEqual(by_symbol["000002"].name, "退市名称")
        self.assertNotIn(
            "000002",
            {item.standard_symbol for item in after_delist},
        )


if __name__ == "__main__":
    unittest.main()
