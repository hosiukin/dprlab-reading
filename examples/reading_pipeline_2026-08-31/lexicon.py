# -*- coding: utf-8 -*-
"""双靶药物词表与组合判定规则（新提取管线共享）。

词表源自 2026-08-31 双靶完整性审计，包含三类曾致漏检的拼写变体：
维莫菲尼、维罗非尼、西妥西单抗（错别字）。修改词表必须记入操作日志。

双靶组合判定（同一治疗方案句内且 intent=执行）：
  EGFR≥1 且 (BRAF≥1 或 MEK≥1)  → 双靶候选
  BRAF≥1 且 MEK≥1（如达拉非尼+曲美替尼）→ 双靶候选（与权威口径一致，含 DAB_TRA）
  单抗EGFR+化疗 / 单BRAF或单MEK / 不同线序贯 → 不是双靶
  黑色素瘤/肺腺癌导向的 DAB+TRA 不属于本项目双靶（癌种导向由人工核对）
"""

import re

DRUG_PATTERNS: dict[str, re.Pattern] = {
    "西妥昔单抗": re.compile(r"西妥[昔西]单抗|西妥昔|爱必妥|cetuximab|C225", re.I),
    "帕尼单抗": re.compile(r"帕尼单抗|panitumumab|维克替比", re.I),
    "尼妥珠单抗": re.compile(r"尼妥珠|nimotuzumab|泰欣生", re.I),
    "维莫非尼": re.compile(r"维莫非尼|维莫菲尼|维罗非尼|vemurafenib|威罗菲尼|佐博伏", re.I),
    "达拉非尼": re.compile(r"达拉非尼|达拉菲尼|dabrafenib|泰菲乐", re.I),
    "康奈非尼": re.compile(r"康奈非尼|康奈菲尼|恩考芬尼|恩考非尼|encorafenib", re.I),
    "曲美替尼": re.compile(r"曲美替尼|trametinib|迈吉宁", re.I),
    "比美替尼": re.compile(r"比美替尼|贝美替尼|binimetinib", re.I),
}

CLASS_OF: dict[str, str] = {
    "西妥昔单抗": "EGFR", "帕尼单抗": "EGFR", "尼妥珠单抗": "EGFR",
    "维莫非尼": "BRAF", "达拉非尼": "BRAF", "康奈非尼": "BRAF",
    "曲美替尼": "MEK", "比美替尼": "MEK",
}

LEXICON_LINES = "\n".join(f"- {d}（{CLASS_OF[d]}类）" for d in DRUG_PATTERNS)


def scan_drugs(text: str) -> dict[str, int]:
    """返回每个药物在文本中的命中次数（供 QC 与抽样参考）。"""
    return {drug: len(pat.findall(text)) for drug, pat in DRUG_PATTERNS.items()}


def is_dual_combo(drugs: list[str]) -> bool:
    """同一方案句内的标准化药物列表是否构成双靶候选。注意必须返回 bool。"""
    classes = {CLASS_OF[d] for d in drugs if d in CLASS_OF}
    dual = ("EGFR" in classes and bool({"BRAF", "MEK"} & classes)) or ({"BRAF", "MEK"} <= classes)
    return bool(dual)


def normalize_drugs(model_names: list, regimen_raw: str = "") -> list[str]:
    """把模型给的药名 + 方案原文串 归一到药典标准名（含别名与 VIC 缩写）。"""
    found: list[str] = []
    for n in model_names or []:
        n = str(n or "").strip()
        if not n:
            continue
        for canon, pat in DRUG_PATTERNS.items():
            if pat.search(n):
                found.append(canon)
                break
    text = regimen_raw or ""
    for canon, pat in DRUG_PATTERNS.items():
        if pat.search(text):
            found.append(canon)
    # VIC = 维莫非尼 + 伊立替康 + 西妥昔单抗（本项目既有方案代码）；与双靶判定相关的两类都加入
    if re.search(r"VIC", text, re.I):
        found += ["维莫非尼", "西妥昔单抗"]
    return sorted(set(found))
