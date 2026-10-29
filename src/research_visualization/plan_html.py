"""Offline HTML timeline and dashboard rendered from a structured portfolio plan.

The page embeds the plan it was rendered from; ``check_plan_html`` compares the page with a plan.
Design notes: docs/modules/3-分析工具/研究可视化模块.md「计划 HTML」.
"""

from __future__ import annotations

import json
import re
from datetime import date
from html import escape
from typing import Any

WIDTH = 1080
LABEL_WIDTH = 230
ROW_HEIGHT = 26
_ACTION_COLORS = {"买入": "#2e7d32", "加仓": "#43a047", "减仓": "#c62828", "清仓": "#8e0000", "持有": "#757575"}
_STATUS_LABELS = {"ok": "符合", "above": "超出", "below": "低于", "unknown": "无法判断"}
_METRIC_LABELS = {"portfolio_weight": "占组合比例", "return_1y": "未来一年收益"}
_DATA_ID = "plan-data"


def render_plan_html(payload: dict[str, Any]) -> str:
    """Self-contained page: inline CSS, SVG and a small filter script; no external resources."""

    plan = payload["plan"]
    labels = payload.get("labels", {})
    nodes = payload["nodes"]
    title = f"组合计划（截止 {plan['as_of_date']}，第 {plan['version']} 版）"
    sections = [
        f"<h1>{_e(title)}</h1>",
        f"<p class='meta'>用户 {_e(plan['user_id'])} · 计划 {_e(plan['plan_id'])} · "
        + ("已入账" if payload.get("recorded") else "预览，未入账")
        + "</p>",
        _list("要点", payload.get("proposal_summary", [])),
        "<h2>时间表</h2>",
        _timeline_svg(plan, nodes),
        _node_table(nodes),
        _targets_table(plan["targets"], labels),
        _reviews(plan["review_nodes"]),
        _list("冲突处理", plan["conflicts"], css="conflicts"),
        _list("被排除的动作", plan["excluded_actions"], css="excluded"),
        _dashboard(payload),
        _list("数据缺口", payload.get("gaps", [])),
        _list("口径与局限", payload.get("limitations", [])),
        f"<script type='application/json' id='{_DATA_ID}'>{_json_for_script(plan)}</script>",
        _SCRIPT,
    ]
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>"
        + "".join(item for item in sections if item)
        + "</body></html>"
    )


def check_plan_html(page: str, plan: dict[str, Any]) -> list[str]:
    """Differences between a rendered page and a plan; empty when they agree."""

    problems = []
    match = re.search(rf"<script type='application/json' id='{_DATA_ID}'>(.*?)</script>", page, re.S)
    if match is None:
        return ["页面没有内嵌计划数据"]
    embedded = json.loads(match.group(1))
    if embedded != json.loads(json.dumps(plan, default=str)):
        problems.append("内嵌计划与当前计划不一致")
    rows = re.findall(r"<tr class='node' data-action-id='([^']*)'", page)
    expected = [item["action_id"] for item in plan["actions"]]
    if sorted(rows) != sorted(_e(item) for item in expected):
        problems.append(f"节点表有 {len(rows)} 行，计划有 {len(expected)} 个动作，或动作编号不一致")
    for node in plan["review_nodes"]:
        if f"data-review-on='{node['review_on']}'" not in page:
            problems.append(f"复盘节点 {node['review_on']} 未出现在页面")
    for target in plan["targets"]:
        text = f"{float(target['low']):.1%}–{float(target['high']):.1%}"
        if text not in page:
            problems.append(f"目标区间 {target['subject_id']} {text} 未出现在页面")
    for item in (*plan["conflicts"], *plan["excluded_actions"]):
        if _e(item) not in page:
            problems.append(f"冲突或排除说明未出现在页面：{item[:30]}")
    return problems


def _timeline_svg(plan: dict[str, Any], nodes: list[dict[str, Any]]) -> str:
    start = date.fromisoformat(plan["as_of_date"])
    dates = [node["valid_until"] for node in nodes] + [item["review_on"] for item in plan["review_nodes"]]
    end = max([date.fromisoformat(item) for item in dates] + [start])
    span = max((end - start).days, 1)
    assets = list(dict.fromkeys(node["label"] for node in nodes))
    height = (len(assets) + 2) * ROW_HEIGHT + 30
    plot = WIDTH - LABEL_WIDTH - 20

    def x(day: str) -> float:
        return LABEL_WIDTH + plot * (date.fromisoformat(day) - start).days / span

    parts = [f"<svg class='timeline' viewBox='0 0 {WIDTH} {height}' role='img' aria-label='计划时间表'>"]
    for month in _month_ticks(start, end):
        parts.append(
            f"<line x1='{x(month):.1f}' y1='20' x2='{x(month):.1f}' y2='{height - 10}' class='grid'/>"
            f"<text x='{x(month):.1f}' y='14' class='tick'>{month[:7]}</text>"
        )
    for row, asset in enumerate(assets):
        y = 30 + row * ROW_HEIGHT
        parts.append(f"<text x='4' y='{y + 16}' class='row-label'>{_e(_clip(asset, 18))}</text>")
        for node in (item for item in nodes if item["label"] == asset):
            left, right = x(node["date"]), x(node["valid_until"])
            color = _ACTION_COLORS.get(node["action"], "#555")
            dashed = " stroke-dasharray='4 2'" if node["kind"] == "conditional" else ""
            fill = "none" if node["kind"] == "conditional" else color
            parts.append(
                f"<rect x='{left:.1f}' y='{y + 4}' width='{max(right - left, 3):.1f}' height='16' fill='{fill}' "
                f"fill-opacity='0.75' stroke='{color}' stroke-width='2'{dashed}>"
                f"<title>{_e(node['date'])}–{_e(node['valid_until'])} {_e(node['action'])} {_e(node['quantity'] or '')}</title></rect>"
            )
    review_y = 30 + len(assets) * ROW_HEIGHT
    parts.append(f"<text x='4' y='{review_y + 16}' class='row-label'>复盘节点</text>")
    for item in plan["review_nodes"]:
        cx = x(item["review_on"])
        parts.append(
            f"<polygon points='{cx:.1f},{review_y + 4} {cx + 6:.1f},{review_y + 12} {cx:.1f},{review_y + 20} "
            f"{cx - 6:.1f},{review_y + 12}' class='review'><title>{_e(item['review_on'])} {_e(item['focus'])}</title></polygon>"
        )
    parts.append(
        f"<text x='{LABEL_WIDTH}' y='{height - 2}' class='legend'>实心：定期节点；虚线框：条件节点；绿色买入或加仓，红色减仓或清仓；菱形：复盘</text></svg>"
    )
    return "".join(parts)


def _node_table(nodes: list[dict[str, Any]]) -> str:
    assets = sorted({node["label"] for node in nodes})
    options = "".join(f"<option>{_e(item)}</option>" for item in assets)
    rows = "".join(
        f"<tr class='node' data-action-id='{_e(node['action_id'])}' data-asset='{_e(node['label'])}' data-kind='{_e(node['kind'])}'>"
        f"<td>{_e(node['date'])}</td><td>{_e(node['valid_until'])}</td>"
        f"<td>{'条件' if node['kind'] == 'conditional' else '定期'}</td><td>{_e(node['label'])}</td>"
        f"<td class='act' style='color:{_ACTION_COLORS.get(node['action'], '#555')}'>{_e(node['action'])}</td>"
        f"<td>{_e(node['quantity'] or '')}</td><td>{_e(node['condition'] or '按时间执行')}</td><td>{_e(node['source'])}</td></tr>"
        for node in nodes
    )
    has_conditional = any(node["kind"] == "conditional" for node in nodes)
    toggle = (
        "<label><input type='checkbox' id='conditional-only'> 只看条件节点</label>"
        if has_conditional
        else "<label><input type='checkbox' id='conditional-only' disabled> 本计划没有条件节点，全部按时间执行</label>"
    )
    return (
        "<h2>节点与动作</h2><div class='filters'>标的 <select id='asset-filter'><option value=''>全部</option>"
        f"{options}</select> {toggle}</div>"
        "<table><thead><tr><th>开始</th><th>截止</th><th>类型</th><th>标的</th><th>动作</th><th>数量规则</th>"
        f"<th>条件</th><th>来源</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _targets_table(targets: list[dict[str, Any]], labels: dict[str, str]) -> str:
    rows = "".join(
        f"<tr><td>{_e(labels.get(item['subject_id'], item['subject_id']))}</td>"
        f"<td>{_e(_METRIC_LABELS.get(item['metric_key'], item['metric_key']))}</td>"
        f"<td>{float(item['low']):.1%}–{float(item['high']):.1%}</td>"
        f"<td>{_e(item['window_start'])}–{_e(item['window_end'])}</td>"
        + (
            f"<td>{float(item['probability']['low']):.0%}–{float(item['probability']['high']):.0%}</td>"
            if item.get("probability")
            else "<td>-</td>"
        )
        + f"<td>{_e(item['basis'])}</td></tr>"
        for item in targets
    )
    return (
        "<h2>目标（区间、概率与依据）</h2><table><thead><tr><th>对象</th><th>指标</th><th>区间</th><th>时间窗</th>"
        f"<th>概率</th><th>依据</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _reviews(nodes: list[dict[str, Any]]) -> str:
    items = "".join(
        f"<li data-review-on='{_e(item['review_on'])}'><b>{_e(item['review_on'])}</b> {_e(item['focus'])}</li>"
        for item in nodes
    )
    return f"<h2>复盘节点与改进方法</h2><ul>{items}</ul>"


def _dashboard(payload: dict[str, Any]) -> str:
    before, after = payload.get("before"), payload.get("after")
    if not before or not after:
        return ""
    parts = ["<h2>组合看板（调整前 / 调整后）</h2><div class='cards'>"]
    for label, left, right in (
        ("权益（含跨境）", before["equity"], after["equity"]),
        ("组合年化波动", before["volatility"], after["volatility"]),
        ("最大单只个股", _weight(before["max_stock"]), _weight(after["max_stock"])),
        ("最大行业", _weight(before["max_industry"]), _weight(after["max_industry"])),
    ):
        parts.append(f"<div class='card'><div class='k'>{_e(label)}</div><div class='v'>{_pct(left)} → {_pct(right)}</div></div>")
    parts.append("</div>")
    classes = {item["label"] for item in before.get("asset_classes", [])} | {item["label"] for item in after.get("asset_classes", [])}
    if classes:
        parts.append("<h3>大类资产</h3>" + _paired_bars(
            sorted(classes),
            {item["label"]: item["weight"] for item in before.get("asset_classes", [])},
            {item["label"]: item["weight"] for item in after.get("asset_classes", [])},
        ))
    if after.get("industries"):
        names = [item["industry"] for item in after["industries"][:8]]
        parts.append("<h3>行业暴露（调整后前 8 名）</h3>" + _paired_bars(
            names,
            {item["industry"]: item["weight"] for item in before.get("industries", [])},
            {item["industry"]: item["weight"] for item in after["industries"]},
        ))
    if after.get("risk_contributions"):
        names = [item["label"] for item in after["risk_contributions"]]
        parts.append("<h3>风险贡献占比</h3>" + _paired_bars(
            names,
            {item["label"]: item["risk_share"] for item in before.get("risk_contributions", [])},
            {item["label"]: item["risk_share"] for item in after["risk_contributions"]},
        ))
    if after.get("stress"):
        names = [item["label"] for item in after["stress"]]
        parts.append("<h3>历史压力情景（组合收益）</h3>" + _paired_bars(
            names,
            {item["label"]: item["portfolio_return"] for item in before.get("stress", [])},
            {item["label"]: item["portfolio_return"] for item in after["stress"]},
        ))
    checks = after.get("preference_checks") or []
    if checks:
        rows = "".join(
            f"<tr><td>{_e(item['label'])}</td><td>{_pct(item.get('value'))}</td><td>{_e(_STATUS_LABELS.get(item['status'], item['status']))}</td></tr>"
            for item in checks
        )
        parts.append(f"<h3>调整后与偏好对照</h3><table><thead><tr><th>项目</th><th>调整后</th><th>状态</th></tr></thead><tbody>{rows}</tbody></table>")
    band = payload.get("return_band")
    if band:
        parts.append(
            f"<p>按目标权重回放 {_e(band['start'])} 至 {_e(band['end'])}：滚动一年收益有 {band['coverage']:.0%} 落在 "
            f"{band['low']:.1%} 到 {band['high']:.1%}（约 {band['independent_years']} 个独立年份，这一比例的区间 "
            f"{band['probability_low']:.0%}–{band['probability_high']:.0%}）。历史分布，不是预测。</p>"
        )
    return "".join(parts)


def _paired_bars(names: list[str], left: dict[str, float], right: dict[str, float]) -> str:
    values = [abs(value) for value in (*left.values(), *right.values())] or [1.0]
    scale = max(values) or 1.0
    row = 22
    height = len(names) * row * 2 + 24
    bar_width = WIDTH - LABEL_WIDTH - 120
    parts = [f"<svg class='bars' viewBox='0 0 {WIDTH} {height}' role='img'>"]
    for index, name in enumerate(names):
        y = 8 + index * row * 2
        parts.append(f"<text x='4' y='{y + 14}' class='row-label'>{_e(_clip(name, 18))}</text>")
        for offset, (series, css) in enumerate(((left, "before"), (right, "after"))):
            value = series.get(name)
            if value is None:
                continue
            width = bar_width * abs(value) / scale
            parts.append(
                f"<rect x='{LABEL_WIDTH}' y='{y + offset * row}' width='{width:.1f}' height='{row - 6}' class='{css}{' neg' if value < 0 else ''}'/>"
                f"<text x='{LABEL_WIDTH + width + 6:.1f}' y='{y + offset * row + 12}' class='val'>{value:.1%}</text>"
            )
    parts.append(f"<text x='{LABEL_WIDTH}' y='{height - 4}' class='legend'>浅色：调整前；深色：调整后</text></svg>")
    return "".join(parts)


def _list(title: str, items: list[str], *, css: str = "") -> str:
    if not items:
        return ""
    rows = "".join(f"<li>{_e(item)}</li>" for item in items)
    return f"<h2>{_e(title)}</h2><ul class='{css}'>{rows}</ul>"


def _month_ticks(start: date, end: date) -> list[str]:
    ticks, year, month = [], start.year, start.month + 1
    while True:
        if month > 12:
            year, month = year + 1, 1
        current = date(year, month, 1)
        if current > end:
            return ticks
        ticks.append(current.isoformat())
        month += 1


def _weight(item: dict[str, Any] | None) -> float | None:
    return None if item is None else item["weight"]


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def _clip(text: str, length: int) -> str:
    return text if len(text) <= length else text[: length - 1] + "…"


def _e(value: Any) -> str:
    return escape(str(value), quote=True)


def _json_for_script(value: Any) -> str:
    # \u003c keeps any "<" in user text from opening or closing tags inside the data block.
    return json.dumps(value, ensure_ascii=False, default=str).replace("<", "\\u003c")


_CSS = """
body{font-family:-apple-system,"Microsoft YaHei","PingFang SC",sans-serif;margin:24px auto;max-width:1120px;color:#222;line-height:1.5}
h1{font-size:22px}h2{font-size:18px;border-bottom:1px solid #ddd;padding-bottom:4px;margin-top:28px}h3{font-size:15px}
.meta{color:#666;font-size:13px}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid #e0e0e0;padding:4px 6px;text-align:left;vertical-align:top}th{background:#f5f5f5}
svg{width:100%;height:auto}.grid{stroke:#eee}.tick,.legend,.val{font-size:11px;fill:#666}.row-label{font-size:12px;fill:#333}
.review{fill:#f9a825}.before{fill:#90caf9}.after{fill:#1565c0}.neg.before{fill:#ef9a9a}.neg.after{fill:#c62828}
.cards{display:flex;gap:12px;flex-wrap:wrap}.card{border:1px solid #ddd;border-radius:6px;padding:8px 12px;min-width:180px}
.card .k{font-size:12px;color:#666}.card .v{font-size:18px}.filters{margin:8px 0;font-size:13px}.act{font-weight:bold}
.conflicts li{color:#6d4c41}.excluded li{color:#b71c1c}
"""

_SCRIPT = """<script>
(function(){
  var select=document.getElementById('asset-filter'),only=document.getElementById('conditional-only');
  function apply(){
    var rows=document.querySelectorAll('tr.node');
    for(var i=0;i<rows.length;i++){
      var row=rows[i],show=(!select.value||row.getAttribute('data-asset')===select.value)&&(!only.checked||row.getAttribute('data-kind')==='conditional');
      row.style.display=show?'':'none';
    }
  }
  select.addEventListener('change',apply);only.addEventListener('change',apply);
})();
</script>"""
