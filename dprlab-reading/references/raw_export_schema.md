# 医院 raw 导出解析规范（n25427 结构）

这份规范对应 `/srv/spark-shared/datasets/n25427/hzq_original/` 的 22 张 CSV，
也适用于同一 HIS 导出格式的其他批次。目标是先正确识别 ID 和表关系，再构建可查询数据库。

## 1. 文件与编码

- 文件名模式：`基线数据.HSK-{分类}-all_{表名}_gbk.csv`（少数为 `基线数据.基线_gbk.csv`）。
- 编码：**GBK**（不是 UTF-8）。
- **前两行不是表头**：
  - 第 1 行：导出标题（如 `固定列信息`、`HSK-检查-all`）；
  - 第 2 行：中文字段名；
  - 第 3 行：机器字段名（真正表头）。
- Python 读取：

```python
pd.read_csv(path, encoding="gbk", header=2, dtype=str, keep_default_na=False)
# 或 csv.reader：next() 两次，第三次是表头
```

- 跳过 `._*`（macOS 元数据文件）。
- 少数表（手术、病史、随访）的第 2 行中文名里包含逗号和引号，必须用真正的 CSV
  解析器，不要用 `split(",")`。

## 2. 主键与四个“号”

| 字段 | 含义 | 使用方式 |
|---|---|---|
| `patient_sn` | 患者唯一流水号（本项目主键） | **所有表用 `patient_sn` 关联**；25,427 例唯一 |
| `patient_id` | 医院患者 ID | 1:1 属性，保留 TEXT；有 2 个空值；**不要拿它当跨表主键** |
| `inpatient_no_id` | 住院号 | 保留前导零（TEXT）；基线/病史表中每个患者 1 个，25,426 非空；**不是每次住院一个号** |
| `outpatient_no_id` | 门诊号 | 保留前导零（TEXT）；25,190 非空；同样不是每次就诊一个号 |
| `vsn_*` | 导出记录/版本号（如 `vsn_CT`、`vsn_discharge_record`） | 行级来源标识，不是患者号 |
| `exam_id` / `pathology_id` / `pathology_exam_id` | 检查/病理报告 ID | 报告级去重与回溯 |

关键结论：

- **跨表关联只用 `patient_sn`**。`patient_id`、住院号、门诊号是患者属性，不是访问/就诊主键。
- 这套导出**没有 visit_id**。住院就诊需要从 `admission_date`/`discharge_date` 构造；
  门诊就诊用 `date_of_note`；同一患者同一天可能有多条记录，需要保留 `source_table + source_row`。
- 住院号/门诊号有前导零，读成整数会损坏匹配；全程用字符串。

## 3. 表清单与字段语义

### 3.1 患者主数据

| 表 | 行数（约） | 关键字段 | 说明 |
|---|---:|---|---|
| `基线数据.基线_gbk.csv` | 25,431 | `patient_sn, patient_id, inpatient_no_id, outpatient_no_id, 基线_gender, 基线_birth_date, 基线_ethnicity, age_first_visit_year, times_of_inhospitalization, asa_level, kps_score, ecog_score, exam_time, temperature, heart_rate, ...` | 患者属性 + 生命体征；同一患者可能多行（体征），建库时患者属性取首个非空 |
| `基线数据.HSK-病史-all_gbk.csv` | 25,429 | `patient_sn, gender, birth_date, inpatient_no_id, ethnicity, nationality, ancestral_home_province, birth_place` | 患者属性/住院号补充；不是就诊记录 |

建库时以 `patient_sn` 去重：患者属性优先取基线，缺失再用病史表补。

### 3.2 就诊与病史文本

| 表 | 行数（约） | 关键字段 | 说明 |
|---|---:|---|---|
| `..._门诊记录_gbk.csv` | 502,936 | `date_of_note, chief_complain, present_illness_history, past_medical_history, family_history, physical_exam_results, auxiliary_exam_results` | 门诊逐次记录；`date_of_note` 是就诊日期 |
| `..._出院记录_gbk.csv` | 132,222 | `admission_date, discharge_date, admission_condition, treatment_process, discharge_condition, discharge_order` | 住院就诊；用入院/出院日期构造 visit |
| `..._现病史_gbk.csv` | 122,026 | `admission_date, present_illness_history` | 住院现病史文本，按入院日期 |
| `..._术后首次病程_gbk.csv` | 34,946 | `note_date, surgery_name, surgery_start_datetime, surgery_end_datetime, anesthesia_method, surgery_body_site, postoperation_treatment, matters_need_caution, record_content` | 术后首次病程 |
| `..._随访-all_gbk.csv` | 25,427 | 与门诊记录同列 | 随访性质的门诊记录；单独标记 `source_table=followup_all` |

注意：`门诊记录` 和 `随访-all` 的列名相同，但含义不同；建库时用 `source_table` 区分，
不要简单地全部合并成“门诊”。

### 3.3 检查/病理/分子

| 表 | 行数（约） | 关键字段 | 说明 |
|---|---:|---|---|
| `..._CT检查_gbk.csv` | 132,428 | `exam_id, exam_date, exam_type, exam_site_sysucc, exam_name_origin, exam_finding, exam_conclusion` | CT |
| `..._MRI检查_gbk.csv` | 58,452 | 同上 | MRI |
| `..._电子结肠镜_gbk.csv` | 54,663 | 同上 | 结肠镜 |
| `..._全部病理_gbk.csv` | 96,543 | `pathology_id, pathology_exam_id, exam_date, exam_name, gross_finding, imaging_finding, imaging_conclusion` | **字段名会误导**：`gross_finding`=大体所见；`imaging_finding`=镜下所见；`imaging_conclusion`=病理诊断 |
| `..._分子免疫标志物-免疫.._gbk.csv` | 593,033 | `record_time, item_name` | **只有项目名和时间，没有结果列**（MLH1/MSH6/MSH2/PMS-2/Ki-67/P53/PDL1 等）。不要因为出现某行就推断阳性/阴性 |
| `..._分子免疫标志物-基因.._gbk.csv` | 181,176 | `record_time, gene_item_name, gene_amplification_report, gene_mutation_report, gene_mutation_type` | 基因检测；`gene_item_name` 常见 BRAF/k-ras/NRAS/TP53/APC/PIK3CA/HER-2 等；`mutation_type` 如 V600E、G12D |
| `..._分子免疫标志物_gbk.csv` | 175,289 | `record_time, source_table, msi_conclusion` | MSI 结论：`MSS`/`MSI-H`/`MSI-L`/`NA`/`未检测到MSI-H` |
| `..._肿瘤标记物检查_gbk.csv` | 810,260 | `test_time, item_name, qualitative_result, test_result` | CEA/CA199/CA724 等；`test_result` 为数值文本，`qualitative_result` 为定性 |

病理里的英文列名是 HIS 导出命名，不是医学英文；按上表中文语义使用。

### 3.4 治疗

| 表 | 行数（约） | 关键字段 | 说明 |
|---|---:|---|---|
| `..._手术_gbk.csv` | 67,788 | `surgery_start_datetime, surgical_name, pre_operative_diagnosis(_norm), post_operative_diagnosis(_norm), description, type_of_anesthesia, surgery_time_length` | 手术记录 |
| `..._病案首页手术_gbk.csv` | 25,485 | `surgery_end_datetime, surgical_name` | 病案首页手术（可作补充） |
| `..._放射治疗医嘱_gbk.csv` | 38,808 | `prescribed_time, order_name, start_time, quantity, quantity_unit, performed_frequency, stop_time` | 放疗医嘱 |
| `..._放射治疗记录_gbk.csv` | 105,605 | `rt_start_date, rt_end_date, rt_technique, target_body_part, rt_site, total_dosage_cgy, dosage_cgy, rt_times` | 放疗执行记录 |
| `..._放疗小结_gbk.csv` | 38,403 | `radiotherapy_technique, radiotherapy_time, radiotherapy_purpose, radiotherapy_untoward_effect` | 放疗小结 |

系统治疗（化疗/靶向/免疫）**不在这 22 张表里**；本项目的方案/双靶信息来自
`reading_df.csv` 的病史原文抽取（见 dprlab-reading 主技能），不要把手术/放疗表当系统治疗表。

### 3.5 随访与结局

| 表 | 行数（约） | 关键字段 | 说明 |
|---|---:|---|---|
| `..._随访-all_大数据随访_gbk.csv` | 25,557 | `last_follow_up_time, is_recurrence, recurrence_time, is_metastasis, metastatic_site, survival_judgment_time, survival_info_source, confidence, is_death, death_date, death_cause_description` | 患者级随访（大数据） |
| `..._随访-all_院内随访_gbk.csv` | 614,688 | `followup_time, whether_dead, followup_method, death_date, followup_state, is_metastasis, is_recur` | 多次院内随访；状态有正常随访/信访/停止随访/不随访/NA |
| `/srv/spark-shared/datasets/followup/followup_final.csv` | 25,427×37 | 死亡/删失/OS + 11 个 QC 标志 | **派生表**，正式生存分析优先用它；5,609 人 `needs_manual_review` |

随访优先级（源自 followup manifest）：

- 死亡日期优先 `surv2`（大数据随访），删失优先 `surv0/surv1`；
- `surv2.last_follow_up_time` **不能当删失日期**；
- `needs_manual_review` 未处理前不得用于正式生存分析；
- 原始 `followup_events` 保留所有来源，便于回溯。

## 4. 建议的 SQLite 研究库

`scripts/build_research_db.py` 会把上面的 raw CSV 流式构建成 SQLite：

```bash
python scripts/build_research_db.py \
  --src /srv/spark-shared/datasets/n25427/hzq_original \
  --out /srv/spark-work/.spark-shared-datasets/n25427/research.sqlite \
  --followup /srv/spark-shared/datasets/followup/followup_final.csv
```

表：

```text
patients            patient_sn 主键 + patient_id/住院号/门诊号/人口学
vitals              生命体征（可多次）
outpatient_notes    门诊记录（source_table 区分 outpatient / followup_all）
inpatient_visits    出院记录（入院/出院日期）
present_illness     住院现病史
postop_courses      术后首次病程
exams               CT/MRI/结肠镜统一表（modality 区分）
pathology           病理（大体/镜下/诊断）
ihc                 免疫组化项目（result_note 明确“原始导出无结果列”）
molecular_gene      基因检测
msi                 MSI 结论
tumor_markers       肿标
surgery             手术
rt_orders/rt_records/rt_summaries  放疗
followup_events     原始随访事件（大数据 + 院内）
outcomes            followup_final 全列 + QC（可选）
build_meta          构建时间、来源、警告
patient_timeline    按患者合并的时间线视图
```

所有表都有 `patient_sn` 索引；`patient_timeline` 用于“这个患者按时间发生了什么”。

### 常用查询

```sql
-- 患者主索引
SELECT * FROM patients WHERE patient_sn = :sn;

-- 时间线
SELECT event_date, event_type, summary FROM patient_timeline
WHERE patient_sn = :sn ORDER BY event_date;

-- 影像
SELECT exam_date, modality, exam_site, conclusion FROM exams
WHERE patient_sn = :sn ORDER BY exam_date;

-- 病理 + 基因
SELECT exam_date, exam_name, diagnosis FROM pathology WHERE patient_sn = :sn;
SELECT record_time, gene_item_name, mutation_report, mutation_type
FROM molecular_gene WHERE patient_sn = :sn;

-- 随访结局
SELECT * FROM outcomes WHERE patient_sn = :sn;
```

## 5. 建库前必须做的 QC

1. `patient_sn` 是否唯一、是否为空；
2. `patient_id` / 住院号 / 门诊号是否保留前导零、是否一对多；
3. 日期能否解析；只有年月/缺日期的值不要补造；
4. 每张表的行数、重复行、空文本比例；
5. 免疫组化表没有结果列——不要用“有行”推断阳性；
6. 随访 QC：`needs_manual_review`、死亡/删失冲突、`last_follow_up_time` 误用；
7. 系统治疗（化疗/靶向/免疫）不在 raw 表，必须从病史原文抽取；
8. 患者数据只在内网/本地处理，不发送到云端，不批量打印患者原文。

## 6. 隐私与权限

- 原始数据目录：`/srv/spark-shared/datasets/n25427/hzq_original/`；
- 访问组：Unix `default`（`sudo usermod -aG default <user>` 后重新登录）；
- 任何工作区都用**绝对路径**访问；工作区 `data/` 只是主工作区的便利链接，不是唯一入口；
- 生成的 `research.sqlite` 仍是患者级数据，权限与 raw 相同，禁止外传。
