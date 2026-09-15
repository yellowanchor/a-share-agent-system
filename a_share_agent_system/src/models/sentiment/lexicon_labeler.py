"""lexicon_labeler — 金融情感词典弱监督打标器
================================================

对 A 股新闻（title + text 概要）进行三分类情感打标:
    positive / negative / neutral

打分机制（可审计，每个样本记录命中的词典项）:
    1. 词典命中: 正/负面词按权重计分，标题命中 ×2（标题信息密度更高）
    2. 句式规则: 正则匹配「同比增长 / 预盈 / 扭亏 / 净流入」等金融句式
    3. 否定处理: 命中词前方 4 字符窗口内出现否定词（未/不/没有…）→ 分数取反
    4. 标签判定: score >= +1.0 → positive; score <= -1.0 → negative; 其余 neutral
    5. 置信度:   confidence = min(1, |score| / 3)，用于后续过滤低置信样本

设计原则:
    - 词典只收「方向明确」的词，避免歧义词（如"停牌"本身中性偏负，仅记 -0.8）
    - 每条样本保留 pos_terms / neg_terms 命中记录，支持人工抽检回溯
    - 纯 Python 实现，126K 条 < 30s
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# 1. 情感词典（word -> weight）
#    权重含义: 2.5=极强信号(跌停/涨停级), 1.5-2=强, 0.5-1=辅助
#    注意: 裸 "ST" 不入词典（大量出现在股票名称中，会引入系统性噪声）
# ---------------------------------------------------------------------------
POSITIVE_WORDS: Dict[str, float] = {
    # 涨跌类
    "涨停": 2.5, "大涨": 2.0, "暴涨": 2.5, "领涨": 1.5, "走强": 1.0,
    "强势": 1.0, "新高": 1.5, "反包": 1.0, "反弹": 0.8, "拉升": 1.2,
    "上涨": 1.2, "收涨": 1.0, "高开": 0.8,
    # 业绩类
    "利好": 2.0, "超预期": 2.0, "好于预期": 2.0, "扭亏": 2.0,
    "预增": 2.5, "预盈": 2.0, "盈利": 1.5, "增长": 0.8, "提升": 0.8,
    # 资本动作类
    "增持": 1.5, "回购": 1.5, "中标": 2.0, "签订": 1.2, "订单": 1.0,
    "合同": 1.0, "获批": 1.5, "批准": 1.0, "分红": 1.0, "派息": 1.0,
    "战略合作": 1.2, "上调": 1.2, "加码": 0.8, "定增": 0.3,
    # 资金面
    "净流入": 1.5, "抢筹": 1.5, "加仓": 1.0, "买入": 0.8, "增持计划": 1.8,
    # 经营面
    "满产": 1.0, "供不应求": 1.5, "涨价": 0.8, "提价": 0.8, "突破": 1.0,
    "拓展": 0.5, "创新高": 1.8, "历史新高": 2.0,
    "摘帽": 2.0, "摘星": 1.5, "脱帽": 1.5, "定点协议": 1.5, "项目定点": 1.5,
    "量价齐升": 1.5, "景气": 0.8, "复牌": 0.5,
    # —— v2 扩充（基于 200 条人工校验的漏检模式）——
    # 分红派现类: "拟10派0.7元"句式词典无法命中"分红/派息"
    "派发": 1.0, "分红派现": 1.2,
    # 再融资/注册类: 定增获注册批复、上市申请获受理
    "注册批复": 1.5, "审核通过": 1.5, "获受理": 1.0, "上市申请": 1.0,
    "注册证书": 1.5, "获同意": 1.0,
    # 并购投资类: 拟收购、参设基金、增资、对外投资
    "拟收购": 1.0, "收购": 0.8, "参设": 1.0, "增资": 0.8, "参股": 0.8,
    "对外投资": 0.8, "入主": 1.0,
    # 生产经营类: 核心供应商、批量生产、下单增加、发明专利
    "核心供应商": 1.0, "批量生产": 1.0, "批产": 1.0, "下单": 0.8,
    "补库存": 0.8, "配套": 0.8, "扩大": 0.8, "自主研发": 0.8,
    "发明专利": 1.0, "提速": 0.8,
}

NEGATIVE_WORDS: Dict[str, float] = {
    # 涨跌类
    "跌停": 2.5, "大跌": 2.0, "暴跌": 2.5, "领跌": 1.5, "走弱": 1.0,
    "新低": 1.5, "跳水": 1.5, "闪崩": 2.5, "腰斩": 2.0, "下挫": 1.0,
    "下跌": 1.2, "收跌": 1.0, "低开": 0.8,
    # 业绩类
    "利空": 2.0, "低于预期": 2.0, "不及预期": 2.0, "预亏": 2.5,
    "亏损": 1.8, "下滑": 1.0, "下降": 0.8, "减少": 0.6, "下调": 1.2,
    # 资本动作/风险类
    "减持": 1.8, "清仓式减持": 2.5, "质押": 0.8, "违规": 2.0,
    "处罚": 2.0, "罚款": 1.5, "立案": 2.5, "警示": 1.5, "退市": 2.5,
    "退市风险": 2.5, "违约": 2.0, "诉讼": 1.2, "仲裁": 1.0,
    "商誉减值": 2.0, "减值": 1.5, "停牌": 0.8, "破发": 1.5,
    "破产": 2.5, "重整": 1.0, "被查": 2.0, "问询": 1.2,
    # 资金面
    "净流出": 1.5, "出逃": 1.5, "抛售": 1.5, "减仓": 1.0, "卖出": 0.8,
    "减持计划": 2.0, "死叉": 1.2,
    # —— v2 扩充（基于 200 条人工校验的漏检模式）——
    "撤离": 1.5, "重挫": 2.0, "萎缩": 1.2,
    # 经营恶化类
    "连亏": 2.0, "失血": 1.2, "辞职": 1.0, "泥潭": 1.2, "停产": 2.0,
    "超标": 1.5, "债务": 0.5,
}

# 程度副词（命中词前方 3 字符内出现 → 权重 ×系数）
_DEGREE: Dict[str, float] = {
    "大幅": 1.5, "显著": 1.3, "明显": 1.2, "略微": 0.6, "小幅": 0.7,
    "罕见": 1.3, "创纪录": 1.5, "翻倍": 1.5, "倍增": 1.4,
}

# 否定词（命中词前方 4 字符窗口 → 分数取反 ×0.9）
_NEGATIONS = ("未", "不", "没有", "难以", "暂未", "并未", "无法", "不再")

# 取消词（负面词前方 12 字符窗口出现 → 该负面词翻正 ×0.9）
# 典型场景: "撤销退市风险警示"（摘帽=利好）"取消处罚" "解除立案"
_CANCEL_WORDS = ("撤销", "取消", "解除", "豁免")
_CANCEL_WINDOW = 12


# ---------------------------------------------------------------------------
# 2. 句式规则（regex -> score）：匹配同比/环比等带方向的结构化表述
# ---------------------------------------------------------------------------
_PATTERNS: List[Tuple[str, str, float]] = [
    # 业绩方向
    (r"同比\s*(?:大幅\s*)?(增长|增加|上升|提高|提升)", "pat_yoy_up", 1.5),
    (r"同比\s*(?:大幅\s*)?(下降|下滑|减少|降低|下跌)", "pat_yoy_down", -1.5),
    (r"环比\s*(增长|增加|上升)", "pat_qoq_up", 1.2),
    (r"环比\s*(下降|下滑|减少)", "pat_qoq_down", -1.2),
    # 预告方向（预增/预盈/预亏 已在词典，此处补充「净利润预增X%」句式变体）
    (r"净利润\s*预(?:增|盈)", "pat_np_forecast_up", 2.0),
    (r"净利润\s*预(?:亏|减|降)", "pat_np_forecast_down", -2.0),
    (r"(?:业绩|净利)[^。]{0,10}(?:翻倍|倍增|创历史)", "pat_record_up", 1.5),
    # 资金方向
    (r"主力[^。]{0,8}净流入", "pat_main_inflow", 1.2),
    (r"主力[^。]{0,8}净流出", "pat_main_outflow", -1.2),
    # 摘帽/摘星（撤销风险警示 = 利好，抵消词典中「警示/退市风险」的误伤）
    (r"撤销(?:其他)?风险警示", "pat_delist_warning_removed", 2.5),
    (r"(?:撤销|取消)\s*退市风险警示", "pat_delist_removed", 3.0),
    # —— v2 扩充（基于 200 条人工校验的漏检模式）——
    # 分红句式: "拟10派1.25元"
    (r"10\s*派\s*[\d.]+\s*元", "pat_dividend", 1.5),
    # "同比增241.85%"（无"长/加"字的变体，如"同比增超三成"）
    (r"同比增(?![长加])", "pat_yoy_up_bare", 1.2),
    # 融资余额增加（杠杆资金进场）
    (r"融资余额[^。]{0,6}增加", "pat_margin_up", 1.2),
    # 增收不增利
    (r"增收不增利", "pat_rev_not_profit", -1.5),
    # 净利润减亏（仍是亏损状态）
    (r"净利润[^。]{0,6}减亏", "pat_np_loss_narrow", -1.5),
    # 跌幅偏离值（异动向下；涨幅偏离在龙虎榜语境多为中性数据播报，不设正向规则）
    (r"跌幅[^。]{0,6}偏离", "pat_deviation_down", -1.2),
]

_COMPILED = [(re.compile(p), name, s) for p, name, s in _PATTERNS]


# ---------------------------------------------------------------------------
# 3. 打标器
# ---------------------------------------------------------------------------
@dataclass
class LabelResult:
    """单条新闻的打标结果（dataclass 便于序列化与审计）"""
    label: str                    # positive / negative / neutral
    score: float                  # 情感得分（正=利好，负=利空）
    confidence: float             # [0,1] 置信度
    pos_terms: List[str] = field(default_factory=list)   # 命中的正面词/句式
    neg_terms: List[str] = field(default_factory=list)   # 命中的负面词/句式

    POS_LABEL = "positive"
    NEG_LABEL = "negative"
    NEU_LABEL = "neutral"


class LexiconLabeler:
    """金融情感词典弱监督打标器

    Usage:
        labeler = LexiconLabeler()
        res = labeler.label(title="浦发银行净利润同比增长4.08%", text="...")
        res.label  # 'positive'
    """

    # 标签判定阈值：|score| 不足该值视为 neutral
    THRESHOLD = 1.0
    # 置信度归一化分母（|score|=3 时 confidence=1）
    CONF_NORM = 3.0
    # 标题命中权重倍数（标题信息密度高于正文概要）
    TITLE_WEIGHT = 2.0

    def __init__(self, threshold: float = None, title_weight: float = None):
        self.threshold = threshold if threshold is not None else self.THRESHOLD
        self.title_weight = title_weight if title_weight is not None else self.TITLE_WEIGHT

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def label(self, title: str, text: str = "") -> LabelResult:
        """对单条新闻打标。标题与正文概要同时扫描，标题命中 ×title_weight。"""
        score = 0.0
        pos_terms: List[str] = []
        neg_terms: List[str] = []

        for content, w in ((title or "", self.title_weight), (text or "", 1.0)):
            s, pt, nt = self._scan(content, w)
            score += s
            pos_terms.extend(pt)
            neg_terms.extend(nt)

        if score >= self.threshold:
            lab = "positive"
        elif score <= -self.threshold:
            lab = "negative"
        else:
            lab = "neutral"

        conf = min(1.0, abs(score) / self.CONF_NORM)
        return LabelResult(label=lab, score=round(score, 3),
                           confidence=round(conf, 3),
                           pos_terms=pos_terms, neg_terms=neg_terms)

    def label_batch(self, records: List[dict]) -> List[LabelResult]:
        """批量打标（records 需含 title 与可选 text 字段）"""
        return [self.label(r.get("title", ""), r.get("text", "")) for r in records]

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _scan(self, content: str, weight: float) -> Tuple[float, List[str], List[str]]:
        """扫描一段文本，返回 (得分, 正面命中, 负面命中)"""
        score = 0.0
        pos_hits: List[str] = []
        neg_hits: List[str] = []

        # --- 词典扫描（长词优先，避免「清仓式减持」被「减持」提前截断计数）---
        for lexicon, hits, sign in (
            (POSITIVE_WORDS, pos_hits, +1),
            (NEGATIVE_WORDS, neg_hits, -1),
        ):
            for word in sorted(lexicon, key=len, reverse=True):
                start = 0
                while True:
                    idx = content.find(word, start)
                    if idx < 0:
                        break
                    w = lexicon[word]
                    # 程度副词加权
                    prefix = content[max(0, idx - 3):idx]
                    for deg, coef in _DEGREE.items():
                        if deg in prefix:
                            w *= coef
                            break
                    # 否定处理：前方 4 字符窗口内出现否定词 → 取反
                    prefix4 = content[max(0, idx - 4):idx]
                    negated = any(n in prefix4 for n in _NEGATIONS)
                    s = w * weight * sign
                    if negated:
                        s = -s * 0.9
                    # 取消词处理（仅负面词）: 前方 12 字符窗口出现撤销/取消等 → 翻正
                    # 如 "撤销退市风险警示"、"取消警示"
                    cancelled = False
                    if sign < 0:
                        prefix12 = content[max(0, idx - _CANCEL_WINDOW):idx]
                        if any(c in prefix12 for c in _CANCEL_WORDS):
                            s = -s * 0.9
                            cancelled = True
                    score += s
                    tag = "(否定)" if negated else ("(撤销)" if cancelled else "")
                    hits.append(word + tag)
                    start = idx + len(word)

        # --- 句式规则扫描 ---
        for pat, name, s in _COMPILED:
            n = len(pat.findall(content))
            if n:
                score += s * weight * n
                (pos_hits if s > 0 else neg_hits).append(f"{name}×{n}")

        return score, pos_hits, neg_hits


# ---------------------------------------------------------------------------
# 自测：python -m src.models.sentiment.lexicon_labeler
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    lb = LexiconLabeler()
    cases = [
        ("浦发银行：2026年半年度净利润309.51亿元 同比增长4.08%", "业绩快报，归母净利润同比增长"),
        ("信托概念下跌1.47%，主力资金净流出12股", "板块走弱"),
        ("某公司未能扭亏，上半年继续亏损", "预亏"),
        ("贵州茅台：中报净利润445.17亿元，同比增长超预期", ""),
        ("股东拟减持不超过2%股份", ""),
        ("友阿股份拟转让参股公司股权 标的去年上半年亏损4618万元", ""),
    ]
    for t, x in cases:
        r = lb.label(t, x)
        print(f"[{r.label:>8}] score={r.score:+.2f} conf={r.confidence:.2f} "
              f"| {t[:30]} | pos={r.pos_terms} neg={r.neg_terms}")
