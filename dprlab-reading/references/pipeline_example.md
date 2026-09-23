# Phase 2e — 范例管线（reading_pipeline，BRAF n316）

**定位：可运行的参考实现，不是必须套用的模板。** 用户数据是 `patient_sn + conclusion`
两列 CSV 时可直接用；字段/任务不同则按 `data_prep.md` + `methodology.md` 自建，把本管线
当作策略的代码样例（prompt 模板、merge 校验、checkpoint 都是现成可抄的）。

- 代码位置：本仓库 `examples/reading_pipeline_2026-08-31/`
  （BRAF n316 项目内为 `scripts/reading_pipeline_2026-08-31/`）
- 依赖：Python ≥ 3.10、`pandas`（`json_repair` 可选）；HTTP 走标准库，无 SDK。
- 任务设计（BRAF n316 四步，prompt 在 `prompts/step{1..4}_*.md`）：
  step1 全身治疗方案行 / step2 疗效评价 / step3 转移+进展事件 / step4 局部治疗+NED。
  prompt 占位符：`{lexicon} {patient_sn} {chunk_idx} {n_chunks} {chunk} {tail}`。
- 领域词表：`lexicon.py`（8 药 + 错拼变体 + 双靶组合规则 + 幻觉剔除）——**换课题先换词表**。
- 影像整理范例：`imaging_prepare.py`（BRAF 专属路径，策略说明见 data_prep.md §影像）。

## 标准命令序列

```bash
PIPE=examples/reading_pipeline_2026-08-31/pipeline.py
INPUT=<你的.csv>          # 两列：patient_sn, conclusion
STAMP=myrun               # 同一次运行的 prepare/run/merge 必须用同一个 stamp

# 0) 前提：lab-connect 转发已启动（本地 18000 -> spark 127.0.0.1:8000），check_connection.py 通过

# 1) 估算调用量与 token
python $PIPE estimate --input $INPUT

# 2) 切块渲染全部 prompt（落盘可审查）
python $PIPE prepare --input $INPUT --stamp $STAMP

# 3) 不联网验证 prompt
python $PIPE run --stamp $STAMP --dry-run --limit 3

# 4) 小样本试跑（人工核对后再放量）
RP_BASE_URL=http://127.0.0.1:18000/v1 RP_MODEL=clinical-qwen36-35b RP_API_KEY=local-vllm \
  python $PIPE run --stamp $STAMP --limit 20 --qwen-no-think

# 5) 全量（中断后重跑同一命令即续跑）
RP_BASE_URL=http://127.0.0.1:18000/v1 RP_MODEL=clinical-qwen36-35b RP_API_KEY=local-vllm \
  python $PIPE run --stamp $STAMP --workers 8 --qwen-no-think

# 6) 合并 + 证据校验 + 患者级表 + QC 报告
python $PIPE merge --input $INPUT --stamp $STAMP
```

（Windows PowerShell 用 `$env:RP_BASE_URL="..."` 逐个设环境变量，或 `--base-url/--model` 参数替代。）

## 输出结构

```text
runs/<stamp>/
├── batches/step{1..4}.jsonl      # 渲染后的 prompt（含 sha256 指纹）
├── responses/responses.jsonl     # append-only checkpoint（含 usage、ok、error）
├── merged/
│   ├── treatment_lines.csv           # 治疗行（药名已对照词表、双靶标记词表复算）
│   ├── response_evaluations.csv      # 疗效评价
│   ├── metastasis_events.csv / progression_events.csv
│   ├── local_treatments.csv / ned_statements.csv
│   └── patient_wide_fact.csv         # 每人一行：has_dual / first_dual_start / best_response / ned_ever
└── qc_report.md                  # 行数、evidence 命中率、机器双靶数、人工复核声明
```

CSV 均为 `utf-8-sig`（Excel 直接打开不乱码）。所有含患者数据的产物**永不入 Git**。

## 产出后必做

1. 读 `qc_report.md`：evidence 命中率 <70% 先修提示词/词表再续跑；
2. 生成人工裁决清单（机器双靶候选等标记逐条核对 evidence，见 methodology.md §10）;
3. 定稿结果拷出 `runs/` 到带日期的目录（如 `data/processed/<日期>_<主题>_v1/`）并附 manifest。
