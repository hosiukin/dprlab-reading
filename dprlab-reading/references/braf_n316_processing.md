# BRAF n316 的实际清洗与派生流程（2026-10-25 核对）

本项目的 `reading_df` 不是"全部病历"的简单拼接，而是按研究问题做过选择。复用到新课题时
必须先确认口径，避免把"门诊选摘"误当成"全部病史"。

数据位置（spark）：`/home/hezq1/data_analysis/队列数据清洗_20260311/`
原始表：`hzq_original/`（GBK，表头第 3 行）；处理代码：`history.py`、`cleaning.ipynb`、
`history_processing/`、`followup_finding/`。

## 1. 门诊病史 → reading_df（两种口径）

### 1a. 全量拼接（25,427 例，crc_full）

`history.py`：

1. 读 `基线数据.HSK-病史-all_门诊记录_gbk`（GBK，`header=None` 后把第 3 行设为列名）；
2. 取列：`patient_sn, 基线_gender, 基线_birth_date, date_of_note, chief_complain,
   present_illness_history, past_medical_history, family_history, physical_exam_results,
   auxiliary_exam_results`；
3. 按 `patient_sn, date_of_note` 排序；按（patient_sn, date_of_note, present_illness_history）
   去重（keep first）；
4. 每条门诊格式化为：
   `【门诊日期】/【门诊主诉】/【门诊现病史】/【门诊既往史】/【门诊家族史】/【门诊体格检查】/【辅助检查结果】`
   + 分隔线；
5. `groupby(patient_sn)` 把该患者**所有**门诊记录用换行拼接成 `conclusion`，输出两列
   `patient_sn, conclusion`（`output_out_history/reading_df.csv`）。

**注意**：crc_full 只含门诊记录，不含住院、病理、检查、随访；也没有审计列。

### 1b. 选摘（316 例，BRAF 队列）

`braf_n316/01_data_processing.ipynb`：同样读门诊记录、同样格式化，但用
`build_formatted_history(group, keep_last=2, keep_longest_middle_and_last=True)` 选摘：

1. 按 `date_of_note` 排序（NaT 放最前，避免误判"最后一次"）；
2. 若最后一次就是最长一次 → 取**最后 2 次**（`keep_last_2`）；
3. 否则取：**最长一次 + 最长与最后一次之间的最长一次 + 最后 2 次**，去重后按时间排序
   （`selection_mode = keep_longest+middle_longest+last_2`，没有中间最长时为
   `keep_longest+last_2`）；
4. 保留审计列：`conclusion_total_len, selected_note_n, selection_mode, last_note_len,
   max_note_len, length_gap_vs_max, last_is_longest, longest_rank_from_last,
   middle_longest_rank_from_last, middle_longest_len`。

**复用建议**：选摘规则要按研究问题改；审计列必须保留，人工复核时才能回答"为什么选这几段"。

## 2. 住院病史（现病史）

`history_processing/00_all_pts_history*.ipynb`：

- 读 `基线数据.HSK-病史-all_现病史_gbk`，取 `patient_sn, admission_date,
  present_illness_history`；
- 按 `admission_date` 排序，按（patient_sn, admission_date, present_illness_history）去重；
- 格式化为 `【入院日期】/【住院现病史】` + 分隔线；
- 复用同一个 `build_formatted_history`（keep_last=2 + 最长/中间最长）→
  `res/all_pts_history.csv`（带审计列）。

**注意**：该函数原为门诊写的（变量名 `date_of_note`），改成住院时要把日期列换成
`admission_date`；不要直接套用未检查列名。

## 3. 病理报告

`cleaning.ipynb`：

- 读 `基线数据.HSK-检查-all_全部病理_gbk`，取 `patient_sn, vsn_pathology, pathology_id,
  exam_date, exam_name, imaging_conclusion`；
- 按 `patient_sn, exam_date` 排序；
- 每条格式化为 `【病理号】/【病理ID】/【检查日期】/【检查名称】/【检查结论】` + 分隔线；
- `groupby(patient_sn)` 拼接 → `output/reading_df.csv`（两列）。
- 后续 `ai_reading/patho/final_reading_result.xlsx` + `processing.py` 把 AI 输出按
  `key: value` 解析成结构化字段（部位、T/N/M、MMR、PNI、BRAF、肝转移、术前治疗等）。

**注意**：这里是"全部病理"的拼接，未做按时间选摘；病理份数多时文本会很长，提取前先
按研究问题筛选（如只看术前/术后首次）。

## 4. 随访日期判定（OS/删失）

`history_processing/002_all_pts_followup.ipynb` 调用
`ai_reading/hsk_workflow/followup_time_utils.build_followup_time_table()`：

输入三张随访表：

- `surv0`：`基线数据.HSK-随访-all_gbk`（门诊记录，`date_of_note` 用于最后门诊时间）；
- `surv1`：`基线数据.HSK-随访-all_院内随访_gbk`（`followup_time, whether_dead, death_date`）；
- `surv2`：`基线数据.HSK-随访-all_大数据随访_gbk`（`last_follow_up_time,
  survival_judgment_time, is_death, death_date`）。

规则（来自函数 docstring）：

1. 三张表分别按 `patient_sn` 聚合成每人一行，再合并到目标患者表；
2. **死亡时间优先用 `surv2.death_date`**（当前规则下最可信）；
3. 未死亡患者的删失时间优先用可靠接触时间：`surv0.date_of_note` 与 surv1 中非死亡随访的
   `followup_time`；
4. `surv2.last_follow_up_time` **不默认用于删失**，只作为质控字段；
   `surv2.survival_judgment_time` 仅在完全没有可靠接触时间时兜底；
5. 输出：`patient_level`（`death_flag_final, death_date_final, last_contact_hard,
   last_alive_contact_hard, last_contact_final, os_date_final` 等）与 `qc_issues`
   （`death_conflict, multiple_death_dates, wide_hard_contact_gap,
   death_without_death_date_flag, alive_without_contact_flag, needs_manual_review` 等）；
   落盘 `followup_info/followup_final.csv`、`followup_info/followup_qc.csv`。

**复用建议**：随访日期是最容易出错的部分，保留 QC 表并逐条处理 `needs_manual_review`，
不要把 `surv2` 的"最后随访"直接当删失日期。

### 4.1 注册的数据集（平台内）

- `followup`（type=table）：`followup_final.csv`，25,427 行 × 37 列，含
  `death_flag_final, death_date_final, death_source_final, last_contact_final,
  last_contact_source_final, os_date_final` 及 11 个 QC 标志；列含义、来源表、派生步骤、
  优先级规则、caveats 全部写在随数据分发的 `followup_manifest.json` 里。
- `followup_qc.csv`：5,609 名 `needs_manual_review=True` 的患者（19 列），正式生存分析前
  必须逐条确认。
- 工作区软链：`data/followup_final.csv`、`data/followup_qc.csv`、`data/followup_manifest.json`。
- 工具：`dataset_info("followup")` 看列含义/规则；`dataset_get(patient_sn, "followup")`
  取单人结局；`dataset_search(keyword, "followup")` 跨列搜索。
- **纪律**：这是结局表不是原始随访记录；`surv2.last_follow_up_time` 不能当删失日期；
  QC 未处理前不得用于正式 Kaplan-Meier/Cox。

## 5. 与本 skill 的关系

- 本 skill 的 `data_prep.md` 给的是通用策略（编码/表头/ID/多文档）；本篇是 BRAF n316 的
  **具体实现口径**，两者互补。
- 在本项目里：`crc_full` = 门诊全量拼接；`braf_n316` = 门诊选摘；`n25427` bundle = 原始表
  （含住院/病理/检查/随访源表）；住院/病理/随访的派生结果在 `res/`、`output/`、
  `followup_info/`，未合并进 reading_df。
- 复用到用户自己的数据时，先与用户确认：用门诊、住院还是两者？选摘还是全量？随访日期
  以哪张表为准？确认后再改写本流程。
