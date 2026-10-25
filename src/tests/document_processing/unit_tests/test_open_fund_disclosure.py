import unittest
from decimal import Decimal

from src.data_src.models import (
    DisclosureDocument,
    DocumentTable,
    DocumentText,
)
from src.document_processing.open_fund_disclosure import (
    OpenFundDisclosureParser,
)
from src.fund_research import DisclosureExtractionStatus


class OpenFundDisclosureParserTest(unittest.TestCase):
    def setUp(self):
        self.parser = OpenFundDisclosureParser()

    def test_profile_extracts_managers_fees_and_benchmark_pages(self):
        snapshot = self.parser.parse_profile(
            fund_asset_id="asset:fund",
            document=_document("profile-doc", "基金产品资料概要"),
            text=DocumentText(
                document_id="profile-doc",
                text_content=(
                    "编制日期：2026年6月11日\n"
                    "送出日期：2026年6月12日"
                ),
                extractor="unit-test",
                page_count=4,
                status="extracted",
            ),
            tables=[
                _table(
                    "profile-doc",
                    0,
                    1,
                    [
                        ["基金简称", "测试基金"],
                        [
                            "下属基金简称",
                            "测试基金A",
                            "下属基金交易代码",
                            "000001",
                        ],
                        [
                            "基金管理人",
                            "测试基金公司",
                            "基金托管人",
                            "测试银行",
                        ],
                        [
                            "基金经理",
                            "甲经理",
                            "开始担任本基金基金经理的日期",
                            "2020年1月2日",
                        ],
                        [
                            "基金经理",
                            "乙经理",
                            "开始担任本基金基金经理的日期",
                            "2025年3月4日",
                        ],
                    ],
                ),
                _table(
                    "profile-doc",
                    1,
                    2,
                    [["业绩比较基准", "测试指数×80%+债券指数×20%"]],
                ),
                _table(
                    "profile-doc",
                    2,
                    4,
                    [
                        ["费用类别", "收费方式/年费率"],
                        ["管理费", "1.2%"],
                        ["托管费", "0.2%"],
                    ],
                ),
            ],
        )

        self.assertEqual(
            snapshot.extraction_status,
            DisclosureExtractionStatus.EXTRACTED,
        )
        self.assertEqual(snapshot.share_class_code, "000001")
        self.assertEqual(
            snapshot.management_fee_rate,
            Decimal("0.012"),
        )
        self.assertEqual(
            snapshot.sales_service_fee_rate,
            Decimal("0"),
        )
        self.assertEqual(
            [item.name for item in snapshot.fund_managers],
            ["甲经理", "乙经理"],
        )
        self.assertEqual(snapshot.page_numbers["benchmark_text"], 2)
        self.assertEqual(
            snapshot.page_numbers["management_fee_rate"],
            4,
        )

    def test_periodic_extracts_cross_page_tables_and_share_classes(
        self,
    ):
        holding_rows = [
            [
                "序号",
                "股票代码",
                "股票名称",
                "数量（股）",
                "公允价值（元）",
                "占基金资产净值比例（%）",
            ],
            *[
                [
                    str(rank),
                    f"0000{rank:02d}",
                    f"股票{rank}",
                    "1,000",
                    str(10000 - rank),
                    str(11 - rank),
                ]
                for rank in range(1, 11)
            ],
        ]
        snapshot = self.parser.parse_periodic(
            fund_asset_id="asset:fund",
            document=_document(
                "periodic-doc",
                "测试基金2026年第2季度报告",
            ),
            text=DocumentText(
                document_id="periodic-doc",
                text_content=(
                    "报告期（2026年4月1日 - 2026年6月30日）"
                ),
                extractor="unit-test",
                page_count=12,
                status="extracted",
            ),
            tables=[
                _table(
                    "periodic-doc",
                    0,
                    2,
                    [
                        [
                            "下属分级基金的基金简称",
                            "测试基金A",
                            "测试基金C",
                        ],
                        [
                            "下属分级基金的交易代码",
                            "000001",
                            "000002",
                        ],
                        [
                            "报告期末下属分级基金的份额总额",
                            "1,000份",
                            "2,000份",
                        ],
                    ],
                ),
                _table(
                    "periodic-doc",
                    1,
                    3,
                    [
                        [
                            "4.期末基金资产净值",
                            "1,234.5",
                            "6 2,469.12",
                        ],
                        [
                            "5.期末基金份额净值",
                            "1.234",
                            "5 1.23456",
                        ],
                    ],
                ),
                _table(
                    "periodic-doc",
                    2,
                    4,
                    [
                        ["测试基金A", "", "", "", "业绩比较基准", "", ""],
                        [None, None, "净值增长率", "业绩比较基准", None, None, None],
                        [
                            "阶段",
                            "净值增长率①",
                            None,
                            None,
                            "收益率标准差",
                            "①－③",
                            "②－④",
                        ],
                        [None, None, "标准差②", "收益率③", None, None, None],
                        [
                            "过去三个月",
                            "-2.50%",
                            "1.00%",
                            "-3.00%",
                            "0.90%",
                            "0.50%",
                            "0.10%",
                        ],
                    ],
                ),
                _table(
                    "periodic-doc",
                    3,
                    5,
                    [
                        [
                            "过去一年",
                            "10.00%",
                            "1.00%",
                            "8.00%",
                            "0.90%",
                            "2.00%",
                            "0.10%",
                        ],
                    ],
                ),
                _table(
                    "periodic-doc",
                    4,
                    6,
                    [
                        ["", "", "", "", "业绩比较基准", "", ""],
                        [None, None, "净值增长率", "业绩比较基准", None, None, None],
                        [
                            "阶段",
                            "净值增长率①",
                            None,
                            None,
                            "收益率标准差",
                            "①－③",
                            "②－④",
                        ],
                        [None, None, "标准差②", "收益率③", None, None, None],
                        [
                            "过去三个月",
                            "-3.00%",
                            "1.00%",
                            "-3.00%",
                            "0.90%",
                            "0.00%",
                            "0.10%",
                        ],
                    ],
                ),
                _table(
                    "periodic-doc",
                    5,
                    8,
                    [
                        ["1", "权益投资", "0.00003亿元", "75.00"],
                        [
                            "7",
                            "银行存款和结算备付金合计",
                            "800",
                            "20.00",
                        ],
                    ],
                ),
                _table(
                    "periodic-doc",
                    6,
                    9,
                    [
                        ["8", "其他资产", "200", "5.00"],
                        ["9", "合计", "4,000", "100.00"],
                    ],
                ),
                _table(
                    "periodic-doc",
                    7,
                    9,
                    holding_rows,
                ),
                _table(
                    "periodic-doc",
                    8,
                    12,
                    [
                        ["项目", "测试基金A", "测试基金C"],
                        [
                            "报告期期初基金份额总额",
                            "900",
                            "1,900",
                        ],
                        [
                            "报告期期间基金总申购份额",
                            "200",
                            "300",
                        ],
                        [
                            "减:报告期期间基金总赎回份额",
                            "100",
                            "200",
                        ],
                        [
                            "报告期期末基金份额总额",
                            "1,000",
                            "2,000",
                        ],
                    ],
                ),
            ],
        )

        self.assertEqual(
            snapshot.extraction_status,
            DisclosureExtractionStatus.EXTRACTED,
        )
        self.assertEqual(snapshot.report_period_end, "2026-06-30")
        self.assertEqual(len(snapshot.share_classes), 2)
        self.assertEqual(
            snapshot.share_classes[0].unit_nav,
            Decimal("1.2345"),
        )
        self.assertEqual(
            snapshot.share_classes[1].net_asset_value,
            Decimal("2469.12"),
        )
        self.assertEqual(len(snapshot.share_flows), 2)
        self.assertEqual(len(snapshot.asset_allocations), 4)
        self.assertEqual(
            snapshot.asset_allocations[0].amount,
            Decimal("3000"),
        )
        self.assertEqual(len(snapshot.top_holdings), 10)
        self.assertEqual(len(snapshot.performance_comparisons), 3)
        self.assertEqual(
            snapshot.performance_comparisons[0].fund_return,
            Decimal("-0.025"),
        )
        self.assertEqual(
            snapshot.performance_comparisons[0].tracking_difference,
            Decimal("0.005"),
        )
        self.assertEqual(
            snapshot.performance_comparisons[1].period_label,
            "过去一年",
        )
        self.assertEqual(
            snapshot.performance_comparisons[1].page_number,
            5,
        )
        self.assertEqual(
            snapshot.performance_comparisons[2].share_class_code,
            "000002",
        )
        self.assertEqual(snapshot.top_holdings[0].page_number, 9)
        self.assertEqual(
            snapshot.top_holdings[0].net_asset_ratio,
            Decimal("0.10"),
        )

    def test_failed_extraction_is_not_treated_as_no_disclosure(self):
        snapshot = self.parser.parse_profile(
            fund_asset_id="asset:fund",
            document=_document("failed-doc", "基金产品资料概要"),
            text=DocumentText(
                document_id="failed-doc",
                text_content="",
                extractor="unit-test",
                page_count=None,
                status="failed",
                error="broken PDF",
            ),
            tables=[],
        )

        self.assertEqual(
            snapshot.extraction_status,
            DisclosureExtractionStatus.EXTRACTION_FAILED,
        )
        self.assertIn(
            "tables:no_extracted_tables",
            snapshot.extraction_errors,
        )


def _document(document_id, title):
    announcement_date = (
        "2026-07-21"
        if "季度报告" in title
        else "2026-06-12"
    )
    return DisclosureDocument(
        document_id=document_id,
        symbol="000001",
        title=title,
        announcement_date=announcement_date,
        category="unit-test",
        source="official-unit-test",
        file_url=f"https://example.test/{document_id}.pdf",
    )


def _table(document_id, table_index, page_number, rows):
    return DocumentTable(
        document_id=document_id,
        table_index=table_index,
        page_number=page_number,
        rows=rows,
        extractor="unit-test",
        status="extracted",
    )


if __name__ == "__main__":
    unittest.main()
