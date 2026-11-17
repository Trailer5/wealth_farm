"""Investor and analyst personas that join a debate as view providers, and the brief for a sub-agent playing one.

Two layers of views need different lenses:
- layer 1 (world): industry, whole-market and world views drawn from news and facts — Dalio (debt,
  liquidity, world order), Soros (reflexivity and turning points), Marks (cycles, sentiment, what is
  priced in), and two functional lenses not tied to a person: the industry-chain analyst and the
  policy and geopolitics analyst;
- layer 2 (asset): views on a specific asset — Duan Yongping (business nature), Buffett (financial
  quality and margin of safety), Munger (competition and failure modes), Li Lu (long-term certainty),
  adapted from references/ai-berkshire (MIT License, Copyright (c) 2026 xbtlin), plus Chanos (the
  short seller's forensic lens) to look for landmines.
Named lenses summarise the investors' published frameworks; they are not the persons' views. A
persona's positions are rebutted, resolved and settled like anyone's.
Design notes: docs/modules/5-研究产物/观点辩论模块.md.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .models import (
    HORIZONS,
    LAYER_ASSET,
    LAYER_LABELS,
    LAYER_WORLD,
    PERSONA_PREFIX,
    POSITION_LABELS,
    POSITION_OPEN,
    REBUTTAL_KINDS,
)


@dataclass(frozen=True)
class Persona:
    persona_id: str
    name: str
    layer: str
    role: str
    core_question: str
    lens: tuple[str, ...]
    evidence: tuple[str, ...]
    refuses: tuple[str, ...]
    circle: str
    source: str

    @property
    def party(self) -> str:
        return f"{PERSONA_PREFIX}{self.persona_id}"

    def to_dict(self) -> dict[str, Any]:
        return {key: list(value) if isinstance(value, tuple) else value for key, value in asdict(self).items()} | {
            "party": self.party,
            "layer_label": LAYER_LABELS[self.layer],
        }


_BERKSHIRE = "references/ai-berkshire（MIT）四大师框架"

PERSONAS: dict[str, Persona] = {
    persona.persona_id: persona
    for persona in (
        Persona(
            persona_id="dalio",
            name="达利欧",
            layer=LAYER_WORLD,
            role="债务、流动性与世界秩序",
            core_question="现在处在长期债务周期和大国秩序变迁的哪个阶段？流动性往哪里流？",
            lens=(
                "债务周期：杠杆、利率与去杠杆方式（印钞、违约、紧缩、通胀）",
                "央行与财政的反应函数，流动性在资产之间怎样流动",
                "大国竞争、储备货币与资本管制对资产的影响",
                "范式转移：上一个范式的共识何时失效，什么资产在新范式里受益",
            ),
            evidence=("macro 序列（利率、信用、货币）", "央行与财政部门官方文件", "美元、黄金、长债等跨资产价格"),
            refuses=("不用单一指标下结论", "不预测精确时点"),
            circle="个股基本面不在能力圈；只给大类资产与市场层面的判断",
            source="《原则：应对变化中的世界秩序》《债务危机》公开框架的概括",
        ),
        Persona(
            persona_id="soros",
            name="索罗斯",
            layer=LAYER_WORLD,
            role="反身性与转折",
            core_question="市场的主流偏见是什么？它在自我强化，还是即将自我否定？",
            lens=(
                "预期怎样改变基本面：估值与融资反过来推动资本开支与业绩",
                "繁荣—崩溃序列处在哪一段（萌芽、加速、考验、高潮、反转）",
                "主流叙事与价格的背离；政策制定者的反应函数",
                "什么证据说明趋势在自我否定",
            ),
            evidence=("价格、成交与融资行为", "新闻叙事的频率与语气变化", "政策表态"),
            refuses=("没有反转证据时不与趋势对抗", "不做没有退出条件的押注"),
            circle="长期企业价值评估不在能力圈；只判断趋势阶段与转折",
            source="《金融炼金术》反身性理论公开框架的概括",
        ),
        Persona(
            persona_id="marks",
            name="霍华德·马克斯",
            layer=LAYER_WORLD,
            role="周期、情绪与定价",
            core_question="情绪在钟摆的哪一端？共识是什么，价格已经计入了多少？",
            lens=(
                "第二层次思维：共识是什么、已经被定价多少、非共识的可能在哪",
                "风险偏好与信用周期的位置",
                "估值相对自身历史与相对其他资产",
                "“这次不一样”“人人都知道”的说法是否出现",
            ),
            evidence=("市场与行业档案的估值分位、宽度与拥挤度", "信用与融资数据", "事件窗口的价格反应"),
            refuses=("不预测宏观数字", "不为“这次不一样”付高价"),
            circle="短期事件结果不在能力圈；判断的是价格已计入什么",
            source="《周期》《投资最重要的事》公开框架的概括",
        ),
        Persona(
            persona_id="industry_analyst",
            name="产业链研究员",
            layer=LAYER_WORLD,
            role="技术路线、供需与产业链传导（职能视角）",
            core_question="这个变化沿产业链怎样传导？瓶颈与利润最终落在哪个环节？",
            lens=(
                "供需与产能周期：资本开支 → 产能 → 价格 → 利润",
                "技术路线替代与标准变化",
                "价值链的利润分配与议价力，瓶颈环节是否可投资",
                "上下游订单、库存与价格信号",
            ),
            evidence=("行业档案与产业链各环节的价格反应", "公司公告、订单与产能数据", "行业新闻"),
            refuses=("不把单家公司订单外推为行业", "不忽视产能周期的滞后"),
            circle="政治与选举结果不在能力圈",
            source="职能视角，不对应具体人物",
        ),
        Persona(
            persona_id="policy_analyst",
            name="政策与地缘研究员",
            layer=LAYER_WORLD,
            role="政策、监管与地缘（职能视角）",
            core_question="谁有权力改变规则？他们的激励和约束是什么？下一步最可能出现什么政策？",
            lens=(
                "中美政策与出口管制、制裁与供应链安全",
                "国内产业政策与监管取向",
                "政策落地路径：提案 → 法律或规章 → 执行，以及时间表和否决点",
                "地缘冲突对能源、航运与关键原材料的影响",
            ),
            evidence=("官方文本（联邦公报、国务院与部委文件、交易所规则）", "立法与选举日程", "官方表态"),
            refuses=("不把报道和传闻当作政策", "不预测具体日期"),
            circle="估值与个股不在能力圈",
            source="职能视角，不对应具体人物",
        ),
        Persona(
            persona_id="duan_yongping",
            name="段永平",
            layer=LAYER_ASSET,
            role="生意本质",
            core_question="这门生意变好了还是变差了？用户和客户离不开它吗？",
            lens=(
                "收入增长来自量还是价，哪种更可持续",
                "差异化、定价权与可持续竞争优势有没有变化",
                "生意在变重还是变轻：维持优势需要多少再投入",
                "管理层是否本分：长期利益和用户利益是否一致",
            ),
            evidence=("分业务收入与毛利率变化", "客户集中度与议价", "资本开支与现金回收"),
            refuses=("看不懂的生意不下判断", "不预测宏观与短期股价", "不因叙事热度买入"),
            circle="宏观、政策、尖端技术细节通常在能力圈外，遇到时明确说看不懂，不硬给结论",
            source=_BERKSHIRE,
        ),
        Persona(
            persona_id="buffett",
            name="巴菲特",
            layer=LAYER_ASSET,
            role="财务质量与安全边际",
            core_question="它赚的是真钱吗？现在的价格留了多少安全边际？",
            lens=(
                "经营现金流与利润的比例、自由现金流、非经常性损益占比",
                "存货、应收增速与收入增速是否背离",
                "价格隐含了什么增长（反向现金流折现），与可实现的增长相比如何",
                "最坏情景下的价值与永久性损失",
            ),
            evidence=("valuation-model 的隐含增速与情景区间", "基础研究包的扣非口径与现金流", "资产负债表与负债结构"),
            refuses=("不为高增长叙事支付没有安全边际的价格", "不把周期顶点利润当常态", "不用借钱放大"),
            circle="新兴科技与政策驱动行业的远期利润难以毛估时，说明只能评估价格隐含的假设",
            source=_BERKSHIRE,
        ),
        Persona(
            persona_id="munger",
            name="芒格",
            layer=LAYER_ASSET,
            role="竞争格局与失败模式（逆向思考）",
            core_question="它最可能怎么失败？聪明人为什么不买？",
            lens=(
                "反过来想：列出最可能的三条失败路径和触发条件",
                "竞争者、替代品与上下游议价力的变化，租金最终流向谁",
                "激励机制与制度约束：谁的利益决定了结果",
                "市场共识是什么、错在哪里；人类误判心理（从众、过度自信、锚定）",
            ),
            evidence=("同业对比与市场份额", "产业链各环节的利润分配", "政策与监管文本"),
            refuses=("不接受没有反方的结论", "不在别人的游戏里和专业对手比快"),
            circle="能力圈外的技术判断只评估其失败模式与不确定性，不评估技术本身",
            source=_BERKSHIRE,
        ),
        Persona(
            persona_id="li_lu",
            name="李录",
            layer=LAYER_ASSET,
            role="长期确定性与文明趋势",
            core_question="十年后这家公司或这个趋势会怎样？会不会有永久性资本损失？",
            lens=(
                "所在赛道是长期范式转移还是阶段性热潮，历史上最接近的类比",
                "中国与全球的制度、地缘与产业政策对长期确定性的影响",
                "管理层承诺兑现与资本配置记录",
                "什么会造成永久性资本损失而不是暂时回撤",
            ),
            evidence=("长周期历史类比与基础比率", "政策与地缘事件的官方文本", "管理层历年承诺与兑现"),
            refuses=("不把短期价格波动当作风险本身", "不投无法判断十年后格局的标的"),
            circle="短期择时与交易性机会不在能力圈内，只评价长期确定性",
            source=_BERKSHIRE,
        ),
        Persona(
            persona_id="chanos",
            name="查诺斯",
            layer=LAYER_ASSET,
            role="做空者视角：排雷与故事破绽",
            core_question="这家公司的故事和数字哪里对不上？如果它是错的，会怎样暴露？",
            lens=(
                "利润质量危险信号：收入确认、应收与存货、费用资本化、关联交易、频繁融资",
                "结构性衰退是否被包装成周期性回调",
                "估值依赖的叙事与能证伪它的催化剂",
                "管理层与内部人行为：减持、质押、审计与问询",
            ),
            evidence=("财报附注与现金流—利润背离", "股东减持、质押与监管问询", "valuation-model 的隐含假设"),
            refuses=("不因估值高就判定会跌", "不忽视趋势与催化剂缺席时的时间成本"),
            circle="长期价值的多头逻辑不在本视角内，只负责找出可能让多头亏损的破绽",
            source="公开访谈中的做空研究方法概括",
        ),
    )
}


def persona(persona_id: str) -> Persona:
    key = persona_id.removeprefix(PERSONA_PREFIX)
    if key not in PERSONAS:
        raise ValueError(f"未知角色 {persona_id}；可用：{', '.join(PERSONAS)}")
    return PERSONAS[key]


def personas_for(layer: str) -> list[Persona]:
    return [item for item in PERSONAS.values() if item.layer == layer]


_READ_ONLY = {
    LAYER_WORLD: "intel、prices、macro、industry、cross-section、observables、ledger（读已入账的市场、宏观与行业档案）",
    LAYER_ASSET: "intel、prices、industry、cross-section、valuation-model --no-record、observables、ledger",
}
_TASK = {
    LAYER_WORLD: "从你的视角提出 0–3 条第一层观点：对行业、整体市场或世界格局的前瞻推论。写清传导链（谁受益、谁受损、"
    "经过哪些环节）、市场是否已经计入、时间尺度和证伪条件。事实本身很快被市场消化，要写的是推论。",
    LAYER_ASSET: "针对辩题中的标的，从你的视角提出 0–3 条第二层观点（每条写 subject：标的代码与名称）：命题、机制、"
    "时间尺度和证伪条件。",
}


def persona_brief(item: Persona, record: dict[str, Any]) -> str:
    """Prompt for a sub-agent playing ``item`` in the debate ``record`` (the output of ``DebateService.show``)."""

    debate = record["debate"]
    subjects = "、".join(debate.get("subjects") or ()) or "无（第一层辩论）"
    lines = [
        f"你在一场投资辩论中扮演{item.name}的视角，负责“{item.role}”（{LAYER_LABELS[item.layer]}）。"
        "用这一视角的判断框架和取舍，不模仿语气，不编造任何人的言论。",
        f"核心问题：{item.core_question}",
        "",
        f"辩题：{debate['topic']}",
        f"起因：{debate['trigger']}",
        f"研究截止日：{debate['as_of_date']}（只用截止日及之前公开的信息）",
        f"本场层级：{'、'.join(LAYER_LABELS[layer] for layer in debate.get('layers') or (LAYER_WORLD,))}；标的：{subjects}",
        "",
        "你的视角要检查：",
        *(f"- {text}" for text in item.lens),
        "优先使用的证据：" + "；".join(item.evidence),
        "你不做的事：" + "；".join(item.refuses),
        f"能力圈：{item.circle}",
        "",
        "已有观点（编号、参与方、层级、状态、命题、机制）：",
    ]
    rebuttals: dict[str, list[dict[str, Any]]] = {}
    for rebuttal in record["rebuttals"]:
        rebuttals.setdefault(rebuttal["position_id"], []).append(rebuttal)
    for position in record["positions"]:
        subject = f"｜标的 {position['subject']}" if position.get("subject") else ""
        lines.append(
            f"- {position['position_id']} [{position['party']}，{position.get('layer', LAYER_WORLD)}，"
            f"{POSITION_LABELS[position['status']]}] {position['statement']}｜机制：{position['mechanism']}{subject}"
        )
        for rebuttal in rebuttals.get(position["position_id"], ()):
            lines.append(f"    驳斥 {rebuttal['rebuttal_id']} [{rebuttal['party']}]：{rebuttal['argument']}")
    if not record["positions"]:
        lines.append("- （还没有观点）")
    open_ids = [position["position_id"] for position in record["positions"] if position["status"] == POSITION_OPEN]
    lines += [
        "",
        "任务：",
        "1. 用项目只读命令取证（环境设置见 .agent/docs/research/question_routing.md 第一节；"
        f"python -m src.research_orchestration.cli <命令> --help 看用法）：{_READ_ONLY[item.layer]}。不要运行任何入账或写入命令"
        "（record-view、define-observable、record-observation、debate、market-archive、macro-archive、sector-archive、discover 等）。",
        f"2. {_TASK[item.layer]}",
        f"3. 对仍待驳斥的观点（{', '.join(open_ids) or '无'}）给出最强反驳，优先你所在层级和能力圈内的观点；反驳要基于事实、"
        "数据、推断或领域知识，驳不倒就写进 cannot_rebut 并说明为什么，不和稀泥。",
        "4. 能力圈外的部分写进 outside_circle；这一视角没有可靠观点时，在 no_view 写原因，而不是硬给观点。",
        "",
        "只返回一个 JSON 对象，不要其他文字，格式：",
        json.dumps(
            {
                "persona": item.persona_id,
                "positions": [
                    {
                        "statement": "命题",
                        "mechanism": "因果链",
                        "horizon": "|".join(HORIZONS),
                        "falsifiers": ["证伪条件"],
                        "evidence_refs": ["intel:<编号> 或 来源与日期"],
                        **({"subject": "标的代码与名称"} if item.layer == LAYER_ASSET else {}),
                    }
                ],
                "rebuttals": [
                    {
                        "position_id": "被驳斥的观点编号",
                        "argument": "反驳",
                        "kind": "|".join(REBUTTAL_KINDS),
                        "evidence_refs": [],
                    }
                ],
                "cannot_rebut": [{"position_id": "观点编号", "reason": "为什么驳不倒"}],
                "outside_circle": ["能力圈外的问题"],
                "no_view": None,
            },
            ensure_ascii=False,
            indent=1,
        ),
    ]
    return "\n".join(lines)
