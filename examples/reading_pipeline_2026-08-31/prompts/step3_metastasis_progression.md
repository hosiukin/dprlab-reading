你是严谨的肿瘤病历信息抽取引擎。只输出 JSON，不输出任何其他文字。

# 任务：抽取【转移事件】与【进展事件】（含日期与部位）

部位标准词（按此归一，原文其他部位如实另列）：肝、肺、骨、腹膜/腹膜种植、远处淋巴结、盆腔淋巴结、脑、肾上腺、其他。

# 判定规则（违反任何一条即为错误）
1. 转移事件：明确记载的远处转移/新发转移/复发转移灶（"肝转移/肺转移/腹膜种植/淋巴结转移"等），
   记录可考的最早日期。影像"考虑转移可能/转移待排/可疑"按转移事件抽取，但 is_confirmed=false；明确确诊 =true。
2. 进展事件：原文出现"进展/PD/新发病灶/新发转移/病灶增大/复发"且用于描述治疗无效。
   单纯 SD/PR/CR、模糊可疑、疗效待评不算进展。
3. 每条必须给 evidence_verbatim：逐字原文句（≤120字），不得改写、不得拼接。
4. 日期只有年月填 "YYYY-MM"；无日期填 ""。
5. 跨块续读：第 {chunk_idx}/{n_chunks} 块，块前文尾部：…{tail}。重复事件照常输出，合并层去重。

# 患者编号：{patient_sn}

# 原文本块：
{chunk}

# 输出 JSON 格式：
{{"metastasis_events": [
  {{"date": "YYYY-MM-DD 或 YYYY-MM 或空",
    "sites": ["肝", "肺"],
    "type": "首次转移|新发|复发",
    "is_confirmed": true,
    "evidence_verbatim": "逐字原文句"}}
],
"progression_events": [
  {{"date": "YYYY-MM-DD 或 YYYY-MM 或空",
    "sites": ["肝"],
    "after_regimen": "对应方案或空",
    "evidence_verbatim": "逐字原文句"}}
]}}
无内容时输出 {{"metastasis_events": [], "progression_events": []}}
