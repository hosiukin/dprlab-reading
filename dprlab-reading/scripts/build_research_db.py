#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把医院导出的 n25427 结构 raw CSV 构建成可查询的 SQLite 研究库。

输入目录应包含 22 张 `基线数据.HSK-*_gbk.csv`（GBK，前两行为标题/中文名，
第三行为机器列名）。输出为规范化表 + patient_sn 索引 + build_meta。

用法：
  python build_research_db.py \
    --src /srv/spark-shared/datasets/n25427/hzq_original \
    --out /srv/spark-shared/datasets/n25427/research.sqlite \
    --followup /srv/spark-shared/datasets/followup/followup_final.csv

注意：脚本只打印聚合计数，不打印患者行；患者数据不要离开内网。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sqlite3
import sys
import time
from pathlib import Path

FILES = {
    "base": "基线数据.基线_gbk.csv",
    "history": "基线数据.HSK-病史-all_gbk.csv",
    "outpatient": "基线数据.HSK-病史-all_门诊记录_gbk.csv",
    "discharge": "基线数据.HSK-病史-all_出院记录_gbk.csv",
    "present": "基线数据.HSK-病史-all_现病史_gbk.csv",
    "postop": "基线数据.HSK-治疗-all_术后首次病程_gbk.csv",
    "ct": "基线数据.HSK-检查-all_CT检查_gbk.csv",
    "mri": "基线数据.HSK-检查-all_MRI检查_gbk.csv",
    "colon": "基线数据.HSK-检查-all_电子结肠镜_gbk.csv",
    "pathology": "基线数据.HSK-检查-all_全部病理_gbk.csv",
    "ihc": "基线数据.HSK-检查-all_分子免疫标志物-免疫.._gbk.csv",
    "gene": "基线数据.HSK-检查-all_分子免疫标志物-基因.._gbk.csv",
    "msi": "基线数据.HSK-检查-all_分子免疫标志物_gbk.csv",
    "markers": "基线数据.HSK-肿标-all_肿瘤标记物检查_gbk.csv",
    "surgery": "基线数据.HSK-治疗-all_手术_gbk.csv",
    "rt_orders": "基线数据.HSK-治疗-all_放射治疗医嘱_gbk.csv",
    "rt_records": "基线数据.HSK-治疗-all_放射治疗记录_gbk.csv",
    "rt_summary": "基线数据.HSK-治疗-all_放疗小结_gbk.csv",
    "fu_big": "基线数据.HSK-随访-all_大数据随访_gbk.csv",
    "fu_hosp": "基线数据.HSK-随访-all_院内随访_gbk.csv",
    "fu_all": "基线数据.HSK-随访-all_gbk.csv",
}


def iter_rows(src: Path, key: str, limit: int | None = None):
    """逐行读取一张 raw 表；GBK，跳过前两行，第三行是机器列名。"""
    path = src / FILES[key]
    if not path.exists():
        print("MISSING", path, file=sys.stderr)
        return
    fh = open(path, encoding="gbk", errors="replace", newline="")
    try:
        reader = csv.reader(fh)
        next(reader)
        next(reader)
        header = [h.strip() for h in next(reader)]
        for i, row in enumerate(reader):
            if limit is not None and i >= limit:
                break
            yield i, dict(zip(header, row))
    finally:
        fh.close()


def g(row: dict, *names: str) -> str:
    for n in names:
        v = row.get(n)
        if v is not None:
            v = str(v).strip()
            if v and v != "NA":
                return v
    return ""


def date10(value: str) -> str:
    s = (value or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS patients (
          patient_sn TEXT PRIMARY KEY,
          patient_id TEXT, inpatient_no TEXT, outpatient_no TEXT,
          gender TEXT, birth_date TEXT, ethnicity TEXT, nationality TEXT,
          ancestral_home_province TEXT, birth_place TEXT,
          age_first_visit_year TEXT, times_of_inhospitalization TEXT,
          asa_level TEXT, kps_score TEXT, ecog_score TEXT
        );
        CREATE TABLE IF NOT EXISTS vitals (
          vital_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, exam_time TEXT,
          temperature TEXT, heart_rate TEXT, respiratory_rate TEXT,
          diastolic_pressure TEXT, systolic_pressure TEXT, pulse TEXT,
          spo2 TEXT, height TEXT, weight TEXT
        );
        CREATE TABLE IF NOT EXISTS outpatient_notes (
          note_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, note_date TEXT,
          chief_complaint TEXT, present_illness TEXT, past_history TEXT,
          family_history TEXT, physical_exam TEXT, auxiliary_exam TEXT,
          source_table TEXT, source_row INTEGER
        );
        CREATE TABLE IF NOT EXISTS inpatient_visits (
          visit_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, admission_date TEXT, discharge_date TEXT,
          admission_condition TEXT, treatment_process TEXT,
          discharge_condition TEXT, discharge_order TEXT,
          source_table TEXT, source_row INTEGER
        );
        CREATE TABLE IF NOT EXISTS present_illness (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, admission_date TEXT,
          present_illness TEXT, source_table TEXT, source_row INTEGER
        );
        CREATE TABLE IF NOT EXISTS postop_courses (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, note_date TEXT, surgery_name TEXT,
          surgery_start_datetime TEXT, surgery_end_datetime TEXT,
          anesthesia_method TEXT, surgery_body_site TEXT,
          postoperation_treatment TEXT, matters_need_caution TEXT,
          record_content TEXT, source_table TEXT, source_row INTEGER
        );
        CREATE TABLE IF NOT EXISTS exams (
          exam_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, modality TEXT, exam_id TEXT,
          exam_date TEXT, exam_type TEXT, exam_site TEXT,
          exam_name TEXT, exam_name_origin TEXT,
          finding TEXT, conclusion TEXT, source_table TEXT
        );
        CREATE TABLE IF NOT EXISTS pathology (
          path_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, pathology_id TEXT, pathology_exam_id TEXT,
          exam_date TEXT, exam_name TEXT, gross_finding TEXT,
          microscopy_finding TEXT, diagnosis TEXT, source_table TEXT
        );
        CREATE TABLE IF NOT EXISTS ihc (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, record_time TEXT, item_name TEXT,
          result_note TEXT, source_table TEXT
        );
        CREATE TABLE IF NOT EXISTS molecular_gene (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, record_time TEXT, gene_item_name TEXT,
          amplification_report TEXT, mutation_report TEXT,
          mutation_type TEXT, source_table TEXT
        );
        CREATE TABLE IF NOT EXISTS msi (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, record_time TEXT, source_table TEXT,
          msi_conclusion TEXT
        );
        CREATE TABLE IF NOT EXISTS tumor_markers (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, test_time TEXT, item_name TEXT,
          qualitative_result TEXT, test_result TEXT, source_table TEXT
        );
        CREATE TABLE IF NOT EXISTS surgery (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, surgery_start_datetime TEXT,
          surgical_name TEXT, pre_operative_diagnosis TEXT,
          pre_operative_diagnosis_norm TEXT, post_operative_diagnosis TEXT,
          post_operative_diagnosis_norm TEXT, description TEXT,
          type_of_anesthesia TEXT, surgery_time_length TEXT,
          source_table TEXT
        );
        CREATE TABLE IF NOT EXISTS rt_orders (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, prescribed_time TEXT, order_name TEXT,
          start_time TEXT, quantity TEXT, quantity_unit TEXT,
          performed_frequency TEXT, stop_time TEXT, source_table TEXT
        );
        CREATE TABLE IF NOT EXISTS rt_records (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, rt_start_date TEXT, rt_end_date TEXT,
          rt_technique TEXT, target_body_part TEXT, rt_site TEXT,
          beam_type TEXT, beam_energy_mev TEXT, total_dosage_cgy TEXT,
          dosage_cgy TEXT, rt_times TEXT, plan_dosage_cgy TEXT,
          source_table TEXT
        );
        CREATE TABLE IF NOT EXISTS rt_summaries (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, radiotherapy_technique TEXT,
          radiotherapy_time TEXT, radiotherapy_purpose TEXT,
          radiotherapy_untoward_effect TEXT, source_table TEXT
        );
        CREATE TABLE IF NOT EXISTS followup_events (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_sn TEXT NOT NULL, source_table TEXT,
          followup_time TEXT, last_follow_up_time TEXT,
          recurrence TEXT, recurrence_time TEXT, metastasis TEXT,
          metastatic_site TEXT, survival_judgment_time TEXT,
          survival_info_source TEXT, confidence TEXT,
          is_death TEXT, death_date TEXT, death_cause TEXT,
          followup_method TEXT, followup_state TEXT
        );
        CREATE TABLE IF NOT EXISTS outcomes (
          patient_sn TEXT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS build_meta (
          key TEXT PRIMARY KEY, value TEXT
        );
        """
    )


def add_columns_from_file(conn: sqlite3.Connection, table: str, header: list[str]) -> None:
    cur = conn.execute("PRAGMA table_info(%s)" % table)
    have = {r[1] for r in cur.fetchall()}
    for col in header:
        if not col or col in have:
            continue
        safe = '"' + col.replace('"', '""') + '"'
        conn.execute("ALTER TABLE %s ADD COLUMN %s TEXT" % (table, safe))
        have.add(col)


def insert_batch(conn, sql, rows):
    conn.executemany(sql, rows)


def insert_stream(conn, sql, rows, batch: int = 2000) -> int:
    """流式插入，避免把 50 万条门诊全文一次性放进内存。"""
    buf = []
    n = 0
    for r in rows:
        buf.append(r)
        n += 1
        if len(buf) >= batch:
            conn.executemany(sql, buf)
            buf = []
    if buf:
        conn.executemany(sql, buf)
    return n


def build_patients(conn, src, limit):
    patients = {}
    vitals = []
    n = 0
    for _, r in iter_rows(src, "base", limit):
        sn = g(r, "patient_sn")
        if not sn:
            continue
        n += 1
        p = patients.setdefault(sn, {})
        for dst, names in {
            "patient_id": ("patient_id",), "inpatient_no": ("inpatient_no_id",),
            "outpatient_no": ("outpatient_no_id",), "gender": ("基线_gender", "gender"),
            "birth_date": ("基线_birth_date", "birth_date"), "ethnicity": ("基线_ethnicity", "ethnicity"),
            "nationality": ("nationality",), "ancestral_home_province": ("ancestral_home_province",),
            "birth_place": ("birth_place",), "age_first_visit_year": ("age_first_visit_year",),
            "times_of_inhospitalization": ("times_of_inhospitalization",),
            "asa_level": ("asa_level",), "kps_score": ("kps_score",), "ecog_score": ("ecog_score",),
        }.items():
            if not p.get(dst):
                p[dst] = g(r, *names)
        vitals.append((sn, g(r, "exam_time"), g(r, "temperature"), g(r, "heart_rate"),
                       g(r, "respiratory_rate"), g(r, "diastolic_pressure"), g(r, "systolic_pressure"),
                       g(r, "pulse"), g(r, "SpO2"), g(r, "height"), g(r, "weight")))
    for _, r in iter_rows(src, "history", limit):
        sn = g(r, "patient_sn")
        if not sn:
            continue
        p = patients.setdefault(sn, {})
        for dst, names in {
            "inpatient_no": ("inpatient_no_id",), "gender": ("gender", "基线_gender"),
            "birth_date": ("birth_date", "基线_birth_date"), "ethnicity": ("ethnicity", "基线_ethnicity"),
            "nationality": ("nationality",), "ancestral_home_province": ("ancestral_home_province",),
            "birth_place": ("birth_place",),
        }.items():
            if not p.get(dst):
                p[dst] = g(r, *names)
    cols = ["patient_sn", "patient_id", "inpatient_no", "outpatient_no", "gender",
            "birth_date", "ethnicity", "nationality", "ancestral_home_province",
            "birth_place", "age_first_visit_year", "times_of_inhospitalization",
            "asa_level", "kps_score", "ecog_score"]
    rows = []
    for sn, p in patients.items():
        rows.append(tuple([sn] + [p.get(c, "") for c in cols[1:]]))
    conn.executemany("INSERT OR REPLACE INTO patients (%s) VALUES (%s)" %
                     (",".join(cols), ",".join("?" * len(cols))), rows)
    conn.executemany("INSERT INTO vitals (patient_sn,exam_time,temperature,heart_rate,respiratory_rate,diastolic_pressure,systolic_pressure,pulse,spo2,height,weight) VALUES (?,?,?,?,?,?,?,?,?,?,?)", vitals)
    print("patients", len(rows), "base_rows", n, "vitals", len(vitals))


def build_notes(conn, src, limit):
    sql_out = "INSERT INTO outpatient_notes (patient_sn,note_date,chief_complaint,present_illness,past_history,family_history,physical_exam,auxiliary_exam,source_table,source_row) VALUES (?,?,?,?,?,?,?,?,?,?)"

    def outpatient():
        for i, r in iter_rows(src, "outpatient", limit):
            sn = g(r, "patient_sn")
            if sn:
                yield (sn, date10(g(r, "date_of_note")), g(r, "chief_complain"),
                       g(r, "present_illness_history"), g(r, "past_medical_history"),
                       g(r, "family_history"), g(r, "physical_exam_results"),
                       g(r, "auxiliary_exam_results"), "outpatient", i)
    print("outpatient_notes", insert_stream(conn, sql_out, outpatient()))

    def followup_notes():
        for i, r in iter_rows(src, "fu_all", limit):
            sn = g(r, "patient_sn")
            if sn:
                yield (sn, date10(g(r, "date_of_note")), g(r, "chief_complain"),
                       g(r, "present_illness_history"), g(r, "past_medical_history"),
                       g(r, "family_history"), g(r, "physical_exam_results"),
                       g(r, "auxiliary_exam_results"), "followup_all", i)
    print("followup_notes", insert_stream(conn, sql_out, followup_notes()))

    sql_visit = "INSERT INTO inpatient_visits (patient_sn,admission_date,discharge_date,admission_condition,treatment_process,discharge_condition,discharge_order,source_table,source_row) VALUES (?,?,?,?,?,?,?,?,?)"

    def visits():
        for i, r in iter_rows(src, "discharge", limit):
            sn = g(r, "patient_sn")
            if sn:
                yield (sn, date10(g(r, "admission_date")), date10(g(r, "discharge_date")),
                       g(r, "admission_condition"), g(r, "treatment_process"),
                       g(r, "discharge_condition"), g(r, "discharge_order"), "discharge", i)
    print("inpatient_visits", insert_stream(conn, sql_visit, visits()))

    sql_present = "INSERT INTO present_illness (patient_sn,admission_date,present_illness,source_table,source_row) VALUES (?,?,?,?,?)"

    def present():
        for i, r in iter_rows(src, "present", limit):
            sn = g(r, "patient_sn")
            if sn:
                yield (sn, date10(g(r, "admission_date")), g(r, "present_illness_history"), "present_illness", i)
    print("present_illness", insert_stream(conn, sql_present, present()))

    sql_post = "INSERT INTO postop_courses (patient_sn,note_date,surgery_name,surgery_start_datetime,surgery_end_datetime,anesthesia_method,surgery_body_site,postoperation_treatment,matters_need_caution,record_content,source_table,source_row) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"

    def postop():
        for i, r in iter_rows(src, "postop", limit):
            sn = g(r, "patient_sn")
            if sn:
                yield (sn, date10(g(r, "note_date")), g(r, "surgery_name"),
                       g(r, "surgery_start_datetime"), g(r, "surgery_end_datetime"),
                       g(r, "anesthesia_method"), g(r, "surgery_body_site"),
                       g(r, "postoperation_treatment"), g(r, "matters_need_caution"),
                       g(r, "record_content"), "postop", i)
    print("postop_courses", insert_stream(conn, sql_post, postop()))


def build_exams(conn, src, limit):
    for key, modality in (("ct", "CT"), ("mri", "MRI"), ("colon", "COLONOSCOPY")):
        rows = []
        for _, r in iter_rows(src, key, limit):
            sn = g(r, "patient_sn")
            if sn:
                rows.append((sn, modality, g(r, "exam_id"), date10(g(r, "exam_date")),
                             g(r, "exam_type"), g(r, "exam_site_sysucc"),
                             g(r, "exam_name"), g(r, "exam_name_origin"),
                             g(r, "exam_finding"), g(r, "exam_conclusion"), key))
        conn.executemany("INSERT INTO exams (patient_sn,modality,exam_id,exam_date,exam_type,exam_site,exam_name,exam_name_origin,finding,conclusion,source_table) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        print("exams", modality, len(rows))


def build_pathology(conn, src, limit):
    rows = []
    for _, r in iter_rows(src, "pathology", limit):
        sn = g(r, "patient_sn")
        if sn:
            rows.append((sn, g(r, "pathology_id"), g(r, "pathology_exam_id"),
                         date10(g(r, "exam_date")), g(r, "exam_name"),
                         g(r, "gross_finding"), g(r, "imaging_finding"),
                         g(r, "imaging_conclusion"), "pathology"))
    conn.executemany("INSERT INTO pathology (patient_sn,pathology_id,pathology_exam_id,exam_date,exam_name,gross_finding,microscopy_finding,diagnosis,source_table) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    print("pathology", len(rows))


def build_molecular(conn, src, limit):
    rows = []
    for _, r in iter_rows(src, "ihc", limit):
        sn = g(r, "patient_sn")
        if sn:
            rows.append((sn, g(r, "record_time"), g(r, "item_name"),
                         "raw export has no result column; do not infer positive/negative", "ihc"))
    conn.executemany("INSERT INTO ihc (patient_sn,record_time,item_name,result_note,source_table) VALUES (?,?,?,?,?)", rows)
    print("ihc", len(rows))

    rows = []
    for _, r in iter_rows(src, "gene", limit):
        sn = g(r, "patient_sn")
        if sn:
            rows.append((sn, g(r, "record_time"), g(r, "gene_item_name"),
                         g(r, "gene_amplification_report"), g(r, "gene_mutation_report"),
                         g(r, "gene_mutation_type"), "gene"))
    conn.executemany("INSERT INTO molecular_gene (patient_sn,record_time,gene_item_name,amplification_report,mutation_report,mutation_type,source_table) VALUES (?,?,?,?,?,?,?)", rows)
    print("molecular_gene", len(rows))

    rows = []
    for _, r in iter_rows(src, "msi", limit):
        sn = g(r, "patient_sn")
        if sn:
            rows.append((sn, g(r, "record_time"), g(r, "source_table"), g(r, "msi_conclusion")))
    conn.executemany("INSERT INTO msi (patient_sn,record_time,source_table,msi_conclusion) VALUES (?,?,?,?)", rows)
    print("msi", len(rows))

    rows = []
    for _, r in iter_rows(src, "markers", limit):
        sn = g(r, "patient_sn")
        if sn:
            rows.append((sn, g(r, "test_time"), g(r, "item_name"),
                         g(r, "qualitative_result"), g(r, "test_result"), "markers"))
    conn.executemany("INSERT INTO tumor_markers (patient_sn,test_time,item_name,qualitative_result,test_result,source_table) VALUES (?,?,?,?,?,?)", rows)
    print("tumor_markers", len(rows))


def build_treatments(conn, src, limit):
    rows = []
    for _, r in iter_rows(src, "surgery", limit):
        sn = g(r, "patient_sn")
        if sn:
            rows.append((sn, g(r, "surgery_start_datetime"), g(r, "surgical_name"),
                         g(r, "pre_operative_diagnosis"), g(r, "pre_operative_diagnosis_norm"),
                         g(r, "post_operative_diagnosis"), g(r, "post_operative_diagnosis_norm"),
                         g(r, "description"), g(r, "type_of_anesthesia"),
                         g(r, "surgery_time_length"), "surgery"))
    conn.executemany("INSERT INTO surgery (patient_sn,surgery_start_datetime,surgical_name,pre_operative_diagnosis,pre_operative_diagnosis_norm,post_operative_diagnosis,post_operative_diagnosis_norm,description,type_of_anesthesia,surgery_time_length,source_table) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    print("surgery", len(rows))

    rows = []
    for _, r in iter_rows(src, "rt_orders", limit):
        sn = g(r, "patient_sn")
        if sn:
            rows.append((sn, g(r, "prescribed_time"), g(r, "order_name"),
                         g(r, "start_time"), g(r, "quantity"), g(r, "quantity_unit"),
                         g(r, "performed_frequency"), g(r, "stop_time"), "rt_orders"))
    conn.executemany("INSERT INTO rt_orders (patient_sn,prescribed_time,order_name,start_time,quantity,quantity_unit,performed_frequency,stop_time,source_table) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    print("rt_orders", len(rows))

    rows = []
    for _, r in iter_rows(src, "rt_records", limit):
        sn = g(r, "patient_sn")
        if sn:
            rows.append((sn, date10(g(r, "rt_start_date")), date10(g(r, "rt_end_date")),
                         g(r, "rt_technique"), g(r, "target_body_part"), g(r, "rt_site"),
                         g(r, "beam_type"), g(r, "beam_energy_mev"), g(r, "total_dosage_cgy"),
                         g(r, "dosage_cgy"), g(r, "rt_times"), g(r, "plan_dosage_cgy"), "rt_records"))
    conn.executemany("INSERT INTO rt_records (patient_sn,rt_start_date,rt_end_date,rt_technique,target_body_part,rt_site,beam_type,beam_energy_mev,total_dosage_cgy,dosage_cgy,rt_times,plan_dosage_cgy,source_table) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    print("rt_records", len(rows))

    rows = []
    for _, r in iter_rows(src, "rt_summary", limit):
        sn = g(r, "patient_sn")
        if sn:
            rows.append((sn, g(r, "radiotherapy_technique"), g(r, "radiotherapy_time"),
                         g(r, "radiotherapy_purpose"), g(r, "radiotherapy_untoward_effect"), "rt_summary"))
    conn.executemany("INSERT INTO rt_summaries (patient_sn,radiotherapy_technique,radiotherapy_time,radiotherapy_purpose,radiotherapy_untoward_effect,source_table) VALUES (?,?,?,?,?,?)", rows)
    print("rt_summaries", len(rows))


def build_followup(conn, src, limit):
    rows = []
    for _, r in iter_rows(src, "fu_big", limit):
        sn = g(r, "patient_sn")
        if sn:
            rows.append((sn, "big_data", "", g(r, "last_follow_up_time"),
                         g(r, "is_recurrence"), g(r, "recurrence_time"),
                         g(r, "is_metastasis"), g(r, "metastatic_site"),
                         g(r, "survival_judgment_time"), g(r, "survival_info_source"),
                         g(r, "confidence"), g(r, "is_death"), g(r, "death_date"),
                         g(r, "death_cause_description"), "", ""))
    conn.executemany("INSERT INTO followup_events (patient_sn,source_table,followup_time,last_follow_up_time,recurrence,recurrence_time,metastasis,metastatic_site,survival_judgment_time,survival_info_source,confidence,is_death,death_date,death_cause,followup_method,followup_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    print("followup_big", len(rows))

    rows = []
    for _, r in iter_rows(src, "fu_hosp", limit):
        sn = g(r, "patient_sn")
        if sn:
            rows.append((sn, "in_hospital", g(r, "followup_time"), "",
                         g(r, "is_recur"), "", g(r, "is_metastasis"), "",
                         "", "", "", g(r, "whether_dead"), g(r, "death_date"),
                         "", g(r, "followup_method"), g(r, "followup_state")))
    conn.executemany("INSERT INTO followup_events (patient_sn,source_table,followup_time,last_follow_up_time,recurrence,recurrence_time,metastasis,metastatic_site,survival_judgment_time,survival_info_source,confidence,is_death,death_date,death_cause,followup_method,followup_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    print("followup_in_hospital", len(rows))


def build_outcomes(conn, followup: Path | None, limit: int | None):
    if not followup or not followup.exists():
        print("outcomes: followup_final.csv not provided; skipped")
        return
    fh = open(followup, encoding="utf-8-sig", errors="replace", newline="")
    try:
        reader = csv.reader(fh)
        header = [h.strip() for h in next(reader)]
        add_columns_from_file(conn, "outcomes", header)
        cols = ['"' + h.replace('"', '""') + '"' for h in header]
        sql = "INSERT OR REPLACE INTO outcomes (%s) VALUES (%s)" % (
            ",".join(cols), ",".join("?" * len(cols)))
        batch = []
        n = 0
        for row in reader:
            if limit is not None and n >= limit:
                break
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            batch.append(tuple(row[:len(header)]))
            n += 1
            if len(batch) >= 2000:
                conn.executemany(sql, batch)
                batch = []
        if batch:
            conn.executemany(sql, batch)
        print("outcomes", n)
    finally:
        fh.close()


def create_indexes(conn):
    stmts = [
        "CREATE INDEX IF NOT EXISTS idx_vitals_sn ON vitals(patient_sn, exam_time)",
        "CREATE INDEX IF NOT EXISTS idx_outpatient_sn ON outpatient_notes(patient_sn, note_date)",
        "CREATE INDEX IF NOT EXISTS idx_inpatient_sn ON inpatient_visits(patient_sn, admission_date)",
        "CREATE INDEX IF NOT EXISTS idx_present_sn ON present_illness(patient_sn, admission_date)",
        "CREATE INDEX IF NOT EXISTS idx_postop_sn ON postop_courses(patient_sn, note_date)",
        "CREATE INDEX IF NOT EXISTS idx_exams_sn ON exams(patient_sn, exam_date)",
        "CREATE INDEX IF NOT EXISTS idx_path_sn ON pathology(patient_sn, exam_date)",
        "CREATE INDEX IF NOT EXISTS idx_ihc_sn ON ihc(patient_sn, record_time)",
        "CREATE INDEX IF NOT EXISTS idx_gene_sn ON molecular_gene(patient_sn, record_time)",
        "CREATE INDEX IF NOT EXISTS idx_msi_sn ON msi(patient_sn, record_time)",
        "CREATE INDEX IF NOT EXISTS idx_markers_sn ON tumor_markers(patient_sn, test_time)",
        "CREATE INDEX IF NOT EXISTS idx_surgery_sn ON surgery(patient_sn, surgery_start_datetime)",
        "CREATE INDEX IF NOT EXISTS idx_rt_orders_sn ON rt_orders(patient_sn, start_time)",
        "CREATE INDEX IF NOT EXISTS idx_rt_records_sn ON rt_records(patient_sn, rt_start_date)",
        "CREATE INDEX IF NOT EXISTS idx_fu_sn ON followup_events(patient_sn, followup_time)",
    ]
    for s in stmts:
        conn.execute(s)
    conn.execute(
        """CREATE VIEW IF NOT EXISTS patient_timeline AS
        SELECT patient_sn, note_date AS event_date, 'outpatient' AS event_type,
               COALESCE(NULLIF(chief_complaint,''), substr(present_illness,1,80)) AS summary,
               'outpatient_notes' AS source_table, note_id AS source_id
        FROM outpatient_notes
        UNION ALL
        SELECT patient_sn, admission_date, 'inpatient', admission_condition, 'inpatient_visits', visit_id FROM inpatient_visits
        UNION ALL
        SELECT patient_sn, exam_date, 'exam:'||modality, COALESCE(NULLIF(conclusion,''), exam_name), 'exams', exam_row_id FROM exams
        UNION ALL
        SELECT patient_sn, exam_date, 'pathology', COALESCE(NULLIF(diagnosis,''), exam_name), 'pathology', path_row_id FROM pathology
        UNION ALL
        SELECT patient_sn, surgery_start_datetime, 'surgery', surgical_name, 'surgery', row_id FROM surgery
        UNION ALL
        SELECT patient_sn, rt_start_date, 'radiotherapy', COALESCE(NULLIF(rt_site,''), target_body_part), 'rt_records', row_id FROM rt_records
        UNION ALL
        SELECT patient_sn, COALESCE(NULLIF(followup_time,''), last_follow_up_time), 'followup', followup_state, 'followup_events', row_id FROM followup_events"""
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--followup", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=None, help="仅用于测试：每张表只取前 N 行")
    args = ap.parse_args()

    if not args.src.is_dir():
        print("source directory not found:", args.src, file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        print("output exists, refusing to overwrite:", args.out, file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(args.out))
    conn.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY; PRAGMA cache_size=-200000;")
    create_schema(conn)
    started = dt.datetime.now().isoformat(timespec="seconds")
    try:
        build_patients(conn, args.src, args.limit)
        build_notes(conn, args.src, args.limit)
        build_exams(conn, args.src, args.limit)
        build_pathology(conn, args.src, args.limit)
        build_molecular(conn, args.src, args.limit)
        build_treatments(conn, args.src, args.limit)
        build_followup(conn, args.src, args.limit)
        build_outcomes(conn, args.followup, args.limit)
        create_indexes(conn)
        meta = {
            "source": str(args.src),
            "built_at": started,
            "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
            "warning_ihc": "raw IHC export has item_name but no result column",
            "warning_followup": "use outcomes/needs_manual_review before survival analysis",
            "patient_key": "patient_sn",
        }
        conn.executemany("INSERT OR REPLACE INTO build_meta(key,value) VALUES (?,?)",
                         [(k, str(v)) for k, v in meta.items()])
        conn.commit()
        conn.execute("ANALYZE")
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        print("BUILD FAILED:", type(e).__name__, e, file=sys.stderr)
        return 1
    conn.close()
    print("written", args.out, "bytes", args.out.stat().st_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
