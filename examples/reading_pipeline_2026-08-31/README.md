# reading_pipeline（范例实现，2026-08-31 / 2026-09-23 提升入库）

> **定位**：这是 [dprlab-reading](../../.zcode/skills/dprlab-reading/SKILL.md) skill 的**范例参考实现**，源自 BRAF n316 项目
> （316 例转移性结直肠癌病史提取，源谱系 `work/reading_pipeline_2026-08-31/`）。它可以直接用于
> 任何"patient_sn + conclusion"两列的 CSV；你的课题字段不同时，把它当作读取策略的可运行样例来改造
> （策略总纲见 skill 的 `references/methodology.md`）。

## 设计要点（针对旧提取 run 的缺陷逐条规避）

| 旧缺陷 | 本管线对策 |
|---|---|
| 8000 字关键词压缩（21% 病史丢文本） | 不截断：4500 字窗口 + 250 字重叠切块，全文覆盖 |
| 证据逐字率低（旧 run 仅 34%） | 每条事件强制 `evidence_verbatim`；merge 层做"逐字原文子串"校验，未命中即 `evidence_hit=false` |
| 建议被执行误收（双靶 3 例假阳性） | 提示词硬规则：建议/拟/可考虑/咨询一律不抽 |
| 药名变体漏检（维莫菲尼/维罗非尼/西妥西单抗） | 药典 `lexicon.py` 含全部变体；merge 层用词表复算双靶组合 |
| 断点/复跑困难 | 按 `s{step}\|patient\|chunk` 键 checkpoint，重跑自动跳过已完成调用 |
| 判定口径不一 | `dual_combo_lexicon` 仅是机器标记，最终判定必须人工复核摘录（联合vs序贯、执行vs建议、癌种导向） |

## 四步提取（BRAF n316 的任务设计，可按需改造 prompt）

| 步骤 | 内容 | 产出 |
|---|---|---|
| step1 | 全部全身治疗方案行（方案原值、标准化药名、起止日期、程数、线语境、双靶候选标记） | treatment_lines.csv |
| step2 | 全部疗效评价（日期、方式、CR/PR/SD/PD/NE） | response_evaluations.csv |
| step3 | 转移事件 + 进展事件（日期、部位、确认状态） | metastasis/progression_events.csv |
| step4 | 局部治疗（名称、日期、靶部位）+ NED 陈述 | local_treatments.csv、ned_statements.csv |

merge 层另产出 `patient_wide_fact.csv`（每人一行：has_dual / first_dual_start / last_dual_end /
best_response / ned_ever）+ `qc_report.md`（行数、evidence 命中率、机器双靶人数、人工复核提示）。
prompt 模板在 `prompts/step{1..4}_*.md`，占位符：`{lexicon} {patient_sn} {chunk_idx} {n_chunks} {chunk} {tail}`。

## 输入格式

CSV（UTF-8），两列必需：

| 列 | 含义 |
|---|---|
| `patient_sn` | 唯一患者标识符（跨住院/门诊稳定） |
| `conclusion` | 病史原文文本（同患者多次文档请先按策略归并/分块，见 skill `references/data_prep.md`） |

## 用法

```bash
# 0) （通过 lab-connect 隧道时先确认转发已启动：本地 18000 -> spark 127.0.0.1:8000）

# 1) 估算调用量与 token
python scripts/reading_pipeline_2026-08-31/pipeline.py estimate --input <你的.csv>

# 2) 切块渲染（全部 prompt 落盘，可审查后再跑）
python scripts/reading_pipeline_2026-08-31/pipeline.py prepare --input <你的.csv> --stamp myrun

# 3) 不联网验证 prompt 渲染
python scripts/reading_pipeline_2026-08-31/pipeline.py run --stamp myrun --dry-run --limit 3

# 4) 真跑（spark 生产后端，经 lab-connect 隧道）
RP_BASE_URL=http://127.0.0.1:18000/v1 RP_MODEL=clinical-qwen36-35b RP_API_KEY=local-vllm \
  python scripts/reading_pipeline_2026-08-31/pipeline.py run --stamp myrun --workers 8 --qwen-no-think
#   - clinical-qwen36-35b = spark 上的 Qwen3.6-35B-A3B-FP8（别名 clinical-qwen3-4b 同模型）
#   - 请求必须带 chat_template_kwargs {"enable_thinking": false}（--qwen-no-think 已含）；temperature 0
#   - 中断后重跑同一命令即续跑；失败调用自动重试（跳过已成功键）
#   - 共享服务器：--workers 8 已够（服务端并发上限 8），大批量前先查服务器是否有人在使用

# 5) 合并 + 证据校验 + 患者级表格 + QC 报告（--input 必须与 prepare 同一份）
python scripts/reading_pipeline_2026-08-31/pipeline.py merge --input <你的.csv> --stamp myrun
```

注意：`run` 与 `merge` 的 `--stamp` 必须一致（默认是当前时刻，跨命令使用时显式传同一个值）。

## Token 估算参考（BRAF n316：316 例、4 步、551 块/步、2204 次调用）

- 输入 ≈ 7.4M–10.8M token（0.6–1.0 token/中文字 + 每次调用 ~1000 token 模板开销；文本每步重发）
- 输出 ≈ 0.9M–3.3M token（上限 1500/次封顶，实际经验 300–800/次）
- 省调用量：只跑必要步骤（`--steps 1,4` 约减半）；先 `--limit 20` 小样例试跑并人工核对 evidence_hit。

## 依赖

Python ≥ 3.10；`pandas`（必需）、`json_repair`（可选，畸形 JSON 兜底）。HTTP 用标准库
`urllib`，无需 openai SDK。`imaging_prepare.py` 另需 `openpyxl`（读 Excel）。

## 影像学检查整理（imaging_prepare.py，BRAF 专属示例）

治疗反应评价需要"病史 + 影像学结论"两条证据线。`imaging_prepare.py` 是 BRAF n316 的
影像数据整理示例（**输入路径为该项目专属，其他课题请参照其策略改写**）：gbk 编码、表头第 3 行
的检查表 → 过滤 CT/MRI、合并 检查所见+检查结论 为 `exam_text`、按 (patient, 日期, 类型, 文本哈希) 去重、
**对齐治疗时间窗**（`days_from_first_systemic`、`in_dual_window` open/closed/before/after）。
通用策略说明见 skill 的 `references/data_prep.md` §影像。

## 其他后端

代码保留 `--backend glm`（智谱 BigModel OpenAI 兼容端点）路径：需要各自的 API key，
并自行确认数据外发合规；spark 本地模型路径（上文）是默认推荐，数据不出内网。

## 状态与谱系

- 2026-08-31：管线在 work/ 完成，estimate/prepare/dry-run/merge 端到端验证通过；
  Qwen3.6-35B 全量重跑（2,204 调用 0 失败，机器双靶 117 人，evidence 命中 73–88%）。
- 2026-09-23：提升入 `scripts/` 并通用化（`--input` 参数、隧道后端文档），作为 dprlab-reading skill 范例。
- 运行产物 `runs/`（患者级数据）永不入 Git。
