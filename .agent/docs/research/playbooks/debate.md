# 观点辩论研究

适用：重大消息或事件之后要形成前瞻判断并落到投资计划；用户提出前瞻观点要检验；用户没有观点、需要系统主动提出观点；需要多个视角（含投资大师角色）一起辩论。事实很快被市场消化，研究的价值在于基于事实与专业知识的前瞻推论；事实只作为辩论的证据。完整范例：`docs/evidence/research_cases/2026-11-15-美国中期选举与黄仁勋离任.md`。

观点分两层：第一层是行业、整体市场与世界观（视角：达利欧、索罗斯、马克斯、产业链研究员、政策与地缘研究员）；第二层是具体标的（视角：段永平、巴菲特、芒格、李录、查诺斯）。第一层立住的推论外推出候选标的，再开第二层辩论。

重大事件（影响一个行业、一年只有几条的级别）必须以决策收尾：买什么、回避什么、多少、什么时候、什么情况离场。“继续观察”不是结论；分歧大时把分歧变成有条件的动作（触发条件可检查）。确实做不出决策，说明研究方法或基础设施有缺口：写进 `blockers` 并告诉用户，作为待开发项。只有常规新闻与日常观察可以没有结论（`significance: routine`）。

## A. 立题与事实

1. 有消息或用户观点时直接立题；没人带观点时先 `debate --action agenda`（字段与读法先看 `debate --help`），从变化信号、常设世界议题新闻和触发的观察点里选 1–3 个议题。
2. `debate --action start --json <path>` 建辩题：第一层写 `layers: ["world"]`；第二层写 `layers: ["asset"]` 与 `subjects`，`parent_debate_id` 指向产生候选的第一层辩论。常规新闻与日常观察写 `significance: routine`，其余按重大事件（默认）。
3. 取事实：海外事件、政策与美股按 `playbooks/global_events.md` 的 A；A 股价格用 `prices`，行业用 `sector-archive`，截面用 `cross-section`。证据写进各条观点与驳斥的 `evidence_refs`。
4. 用户原话：按 `playbooks/global_events.md` 的 B 先入账原话，再 `debate --action position` 写成 `party=user` 的观点。

## B. 提出观点

5. Agent 写自己的观点（`party=agent`）：命题、因果链、时间尺度、证伪条件；第二层观点写 `layer` 与 `subject`。
6. 各视角：`debate --action personas --layer <层>` 看本层视角；每个视角 `debate --action brief --debate-id <id> --persona <角色>` 取提示词，交给子代理并行扮演（环境没有子代理时逐个扮演，回答里注明）。子代理只读取证、按提示词返回 JSON；交代子代理同时把 JSON 写到 `temp/<研究目录>/persona_<角色>.json`，取证输出也存到同一目录。子代理返回为空时，再派一个子代理读这些取证输出补交 JSON，不必从头取证。
7. `debate --action import --persona <角色> --json <返回的 JSON>` 写入；视角没有可靠观点时 JSON 写 `no_view`，也记为参与。`cannot_rebut` 与 `outside_circle` 在回答里转述。
8. `debate --action show` 的 `coverage`：本层还有未参与的视角时按 `next_actions` 补齐；确实无法补齐的写进结论的局限。

## C. 互相驳斥

9. 每条观点都要有来自其他参与方的最强反驳：`debate --action rebut`，标明事实、推断或领域知识并附证据。先找反方证据，不顺着任何一方找支持。
10. 观点方回应：`debate --action answer`（接受驳斥，或给出反驳依据）。
11. 判定：`debate --action resolve`（立住、被驳倒、撤回）；被部分驳倒的写修正版观点（`supersedes`），再接受驳斥。驳不倒的观点（包括用户的）作为本阶段有效推论。

## D. 外推与剪枝

12. 以第一层立住的推论为前提，沿产业链与资产映射外推候选方向（领域内找标的按 `playbooks/sector.md` 与 `discover`）。
13. 每个方向检验三件事：租金归谁（瓶颈是否可买）、是否已定价（`valuation-model` 隐含增速、`sector-archive` 估值分位）、能否投资（上市与可买渠道）。剪枝理由写成 Agent 观点并接受驳斥。
14. 留下的候选标的开第二层辩论（第 2 步），用第二层视角驳斥与补充。

## E. 决策与验证节点

15. 第二层立住的方向按 `mainline.md`（单标的）或 `playbooks/portfolio.md`（组合）回答是否、何时、多少：分批条件、仓位上限（损失预算 ÷（到价格退出线的距离 + 跳空余量），口径见 `position-guidance --help`；上限不是目标）、失效条件。
16. 入账：立住的推论与 Agent 的选股判断改写为可结算命题（`record-view`；事件先 `define-observable`），用 `debate --action resolve` 或 `debate --action link` 关联账本条目。
17. 观察点：每个关键推论写观察点、触发条件和对应动作（`debate --action signpost`，refs 写 `obs:<id>` 或账本条目，有日期的写 `due_on`）；计划改变时用 `replaces` 取代旧观察点。
18. `debate --action conclude` 写阶段结论与 `decisions`（重大事件必填，字段见 `debate --help`）；第一层的决策可以是行业或资产类别的买入、回避，第二层落到标的。`debate --action show` 的 `gaps` 为空或已在回答中说明。回答用户：两层各自被驳倒、修正、立住的观点，未参与的视角，机会与剪枝理由，决策，观察点表。

## F. 跟踪与复盘

19. `routine --user-id <id>` 的 `steps.debates` 列出到期或有新观测、新结算的观察点：按触发条件核对，成立则执行对应动作；推论被推翻时开新一轮辩论（`parent_debate_id`）。超过 7 天没有第一层辩论时，`next_actions` 会提示运行议程。
20. `debate --action scorecard` 看用户、Agent 与各视角观点的结算命中情况；样本少时不作为依据。
