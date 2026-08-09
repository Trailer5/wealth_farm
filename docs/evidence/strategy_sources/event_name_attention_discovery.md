# 事件—名称注意力外溢发现

## 策略身份

- `strategy_id`: `event_name_attention_discovery_v1`
- 时间尺度：短期事件发现
- 输出：研究候选、淘汰理由和短期研究任务草稿；不是买入建议

## 机制

1. `similar_name_attention_spillover`：事件主体、关键词与证券名称相似时，有限注意力可能向无经济关系证券外溢。
2. `salient_event_abnormal_demand`：高关注事件附近，相似名称证券可能出现异常成交和价格压力。
3. `consensus_coordination`：显著事件和简单叙事可形成无需显式串谋的协调焦点，参与者可能交易对后续参与者的预期而非真实关系。
4. `short_horizon_reversal`：名称驱动的价格压力可能在事件热度下降后反转，但持续时间并不稳定。
5. `chinese_phonetic_matching`：中文场景需要同时检查汉字、拼音、首字母和人工确认的组合短语。
6. `cross_language_acronym_attention`：中文证券名称拼音首字母可能与英文热门概念缩写重合，形成跨语言协调叙事。
7. `brand_name_association`：名称外溢也可能先影响品牌搜索和情绪；关注变化不等于销量或证券收益变化。

## 论文来源

### Balashov and Nikiforov

- 标题：How much do investors trade because of name/ticker confusion?
- URL：https://doi.org/10.1016/j.finmar.2019.06.002
- 固定引用：DOI `10.1016/j.finmar.2019.06.002`
- 用途：名称/代码混淆、异常成交和短期价格压力机制。
- 适用性：提供可审计的名称相似、成交共振和事件窗口机制；市场结构并非 A 股，参数不能直接迁移。
- 不采用：固定相似度阈值、美国市场分钟窗口和收益数量级。

### Qiu, Wu and Zhang

- 标题：In name only: Information spillovers among Chinese firms with similar stock names during earnings announcements
- URL：https://doi.org/10.1016/j.jcorpfin.2021.102015
- 固定引用：DOI `10.1016/j.jcorpfin.2021.102015`
- 用途：中国市场名称显著性、有限注意力和无关公司信息外溢。
- 适用性：支持在 A 股中研究相似名称；原研究围绕公司盈利公告，不证明所有社会事件均有效。
- 不采用：把盈利公告结论直接外推到政治、体育、航天或社会新闻。

### Rashes

- 标题：Massively Confused Investors Making Conspicuously Ignorant Choices
- URL：https://doi.org/10.1111/0022-1082.00394
- 固定引用：DOI `10.1111/0022-1082.00394`
- 用途：相似标识造成无信息需求冲击和数日内价格偏离的经典机制。
- 不采用：单一案例不能当作普遍胜率或可交易性证明。

## 公开实现

### python-pinyin

- 仓库：https://github.com/mozillazg/python-pinyin
- 固定引用：release `v0.55.0`
- 许可证：MIT
- 审阅路径：`pypinyin/core.py`、`pypinyin/standard.py`、`LICENSE`
- 可借鉴：中文词组转无声调拼音、首字母和多音字候选。
- 不采用：拼音相同不等于投资者会建立事件联想；依赖只负责候选生成，不负责候选入选。

事件研究统计复用项目已有实现；不引入依赖 Yahoo Finance 或外部因子命名的事件研究仓库。

### Corona brand sales study

- 标题：Product sales and unintentional name association with the coronavirus pandemic
- URL：https://doi.org/10.1002/jaa2.18
- 固定引用：DOI `10.1002/jaa2.18`
- 用途：区分品牌搜索、口碑、购买意向和实际销售。
- 反证：Corona 名称产生了舆论联想，但实际零售销售没有出现媒体声称的名称驱动下降。

## 反证

### Davies, Hillier and Thamm

- 标题：Investor confusion and similarly identified securities
- URL：https://doi.org/10.1111/j.1467-629x.2007.00223.x
- 固定引用：DOI `10.1111/j.1467-629x.2007.00223.x`
- 结论：英国样本只发现有限证据，效果可能仅存在于极短时间。
- 对实现的约束：名称相似只能生成待验证候选；必须再有市场异常和经济无关证据，不能预设普遍有效。

## 实现边界

1. 首版事件锚点必须人工或 Agent 确认并带来源，不自动从全网判断“真正大事”。
2. 每个语言匹配必须保存可读解释，不使用不可审计的单一综合分数。
3. “未发现业务关系”不等于“确认无关”；证据不足单独输出。
4. 同期公司公告、行业主题、公司行动或其他市场叙事能够直接解释异动时，样本必须标为污染，不能归因于名称。
5. 事件后已经涨停的正向炒作不包装成可执行买入机会。
6. A 股个股做空受限，正向错配的回落主要作为风险观察；负向误杀才可能进入做多研究。
7. 历史验证按事件族留出并处理事件重叠、多重检验、T+1、涨跌停、费用和滑点。
