你是严谨的肿瘤病历信息抽取引擎。只输出 JSON，不输出任何其他文字。

# 任务：抽取【全身药物治疗方案行】（所有线，不限首线）

药典（标准化名称按此归一）：
{lexicon}

# 判定规则（违反任何一条即为错误）
1. 只抽取【执行】过的治疗：原文有"行/给予/予/口服/应用/化疗N程/C1……方案"等执行表述。
   "建议/拟/可考虑/准备/计划/咨询/介绍临床研究/因费用未换"等一律不算执行，跳过。
2. 每个方案行必须给出 evidence_verbatim：从原文逐字复制的支撑句（≤120字，含方案名与起止/程数信息），不得改写、不得拼接两处。
3. 同一方案句内若同时含 EGFR 类 + BRAF/MEK 类药物，或 BRAF 类 + MEK 类药物，is_dual_target_candidate=true；
   单独抗EGFR+化疗、单独BRAF或MEK、不同时间先后使用（序贯）均 =false。判不准就填 false 并在 notes 说明。
4. intent 一律按原文证据填："执行"；仅建议的不出现在输出里。
5. 原文只有年月时 start_date 填 "YYYY-MM"；完全无日期填 ""。
6. line_context 按原文语境填：转移后 / 辅助 / 新辅助 / 术后 / 不确定。
7. 跨块续读：本块是患者长文本的第 {chunk_idx}/{n_chunks} 块，块首可能与上一块末尾重叠。
   与上一块重复出现的事件照常输出，合并层会去重；事件在块内被截断时在 continues_next_chunk 填 true。

# 患者编号：{patient_sn}（第 {chunk_idx}/{n_chunks} 块；块前文尾部：…{tail}）

# 原文本块：
{chunk}

# 输出 JSON 格式：
{{"treatment_lines": [
  {{"regimen_raw": "原文方案串",
    "drugs_standardized": ["药典内标准化药名", "..."],
    "start_date": "YYYY-MM-DD 或 YYYY-MM 或空",
    "end_date": "同上或空",
    "cycles": "N程 或 空",
    "line_context": "转移后|辅助|新辅助|术后|不确定",
    "is_dual_target_candidate": false,
    "evidence_verbatim": "逐字原文句",
    "continues_next_chunk": false,
    "notes": ""}}
]}}
无内容时输出 {{"treatment_lines": []}}
