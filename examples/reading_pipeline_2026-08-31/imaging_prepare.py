#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""imaging_prepare：整理 CT/MRI 影像学检查并对齐治疗时间窗。

整理方式参照 HAQ 项目 scripts/005_exam.ipynb：exam_type 过滤、exam_finding+exam_conclusion
合并为 exam_text、保留 patient_sn+exam_date+exam_type+exam_text。

本项目增强：对齐治疗时间窗（用户重点）——
  - 首次转移后全身治疗起点（run33 导出，316 人）
  - 双靶窗口 [dual_start, dual_end]（人工锚点 74 人；结束日期仅极少数有值 → 开口窗，
    标记 in_dual_window="open"；结束日期待新管线 step1 补齐后重跑本脚本即得闭口窗）
  - 可选 --treatment-lines <step1输出csv>：用全量治疗方案行构建精确窗口

输入（只读）：
  hzq_original/基线数据.HSK-检查-all_{CT,MRI}检查_gbk.csv（gbk，表头第 3 行）
  curated run33 导出 / merged_followup / anchor review / run21 详表
输出：
  data/processed/2026-08-31_imaging_exam_v1/（imaging_exam_organized.csv + 窗口覆盖汇总 + manifest）
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FULL = REPO / "data/raw/2026-08-30_spark_braf_n316_full"
CUR = REPO / "data/processed/2026-08-30_spark_extraction_curated_v1"
OUT_DEFAULT = REPO / "data/processed/2026-08-31_imaging_exam_v1"


def sha256_of(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_exam(kind: str, pids: set[str]) -> pd.DataFrame:
    f = FULL / "hzq_original" / f"基线数据.HSK-检查-all_{kind}检查_gbk.csv"
    df = pd.read_csv(f, encoding="gbk", header=2)
    df = df[df["patient_sn"].astype(str).isin(pids)].copy()
    df["exam_type"] = df["exam_type"].fillna(kind)
    df["exam_date_dt"] = pd.to_datetime(df["exam_date"], errors="coerce")
    df["exam_date_day"] = df["exam_date_dt"].dt.strftime("%Y-%m-%d")

    def merge_text(row) -> str:
        finding = str(row["exam_finding"]).strip() if pd.notna(row["exam_finding"]) else ""
        conclusion = str(row["exam_conclusion"]).strip() if pd.notna(row["exam_conclusion"]) else ""
        parts = []
        if finding:
            parts.append("影像所见：\n" + finding)
        if conclusion:
            parts.append("影像结论：\n" + conclusion)
        return "\n\n".join(parts)

    df["exam_text"] = df.apply(merge_text, axis=1)
    df["exam_text_sha1"] = df["exam_text"].apply(lambda s: hashlib.sha1(s.encode("utf-8")).hexdigest()[:12])
    keep = df[
        [
            "patient_sn", "exam_date", "exam_date_day", "exam_type", "exam_site_sysucc",
            "exam_name_origin", "exam_id", "exam_text", "exam_text_sha1", "exam_date_dt",
        ]
    ].rename(columns={"exam_site_sysucc": "exam_site"})
    return keep


def load_windows(pids: set[str], treatment_lines_csv: str | None):
    """返回 (first_sys: dict, dual: dict[(start, end|None)], window_source: str)"""
    # 首次转移后全身治疗起点（run33 导出）
    r33 = pd.read_excel(CUR / "2026-08-30_03_run33_post_metastasis_first_systemic_export_v1.xlsx")
    date_col = next(c for c in r33.columns if "start" in c.lower() and "date" in c.lower())
    first_sys = {
        str(r["patient_sn"]): (None if pd.isna(r[date_col]) else str(r[date_col])[:10])
        for _, r in r33.iterrows()
        if str(r["patient_sn"]) in pids
    }

    dual: dict[str, tuple[str, str | None]] = {}
    window_source = "provisional: dual anchor (74) + run21 end (sparse)"
    mf = pd.read_excel(CUR / "2026-08-30_06_merged_followup_v1.xlsx")
    ends = {}
    try:
        r21 = pd.read_excel(FULL / "runs/detailed_dual_target_course_run/21_detailed_dual_target_course.xlsx")
        for _, r in r21.iterrows():
            if pd.notna(r.get("first_dual_targeted_end_date")):
                ends[str(r["patient_sn"])] = str(r["first_dual_targeted_end_date"])[:10]
    except Exception:  # noqa: BLE001
        pass
    for _, r in mf.iterrows():
        pid = str(r["patient_sn"])
        if pd.isna(r.get("start_date")):
            continue
        dual[pid] = (str(r["start_date"])[:10], ends.get(pid))

    if treatment_lines_csv:
        tl = pd.read_csv(treatment_lines_csv)
        need = {"patient_sn", "start_date", "end_date", "dual_combo_lexicon"}
        if not need.issubset(tl.columns):
            sys.exit(f"--treatment-lines must contain columns {need}")
        dual = {}
        for _, r in tl[tl["dual_combo_lexicon"] == True].iterrows():  # noqa: E712
            pid = str(r["patient_sn"])
            s, e = str(r["start_date"])[:10], ("" if pd.isna(r["end_date"]) else str(r["end_date"])[:10])
            if not s:
                continue
            if pid in dual and dual[pid][0] <= s:
                dual[pid] = (dual[pid][0], e or dual[pid][1])
            else:
                dual[pid] = (s, e)
        window_source = f"exact: from treatment lines {treatment_lines_csv}"
    return first_sys, dual, window_source


def to_ts(s: str):
    try:
        t = pd.Timestamp(s)
        return None if pd.isna(t) else t
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--treatment-lines", default=None, help="pipeline step1 输出的 treatment_lines.csv（精确窗口）")
    args = ap.parse_args()

    pids = set(
        pd.read_csv(CUR / "2026-08-30_01_reading_df_source_text_v1.csv")["patient_sn"].astype(str)
    )
    parts = [load_exam(k, pids) for k in ("CT", "MRI")]
    exam = pd.concat(parts, ignore_index=True)
    before = len(exam)
    exam = exam.drop_duplicates(subset=["patient_sn", "exam_date_day", "exam_type", "exam_text_sha1"])
    exam = exam.sort_values(["patient_sn", "exam_date_dt"]).reset_index(drop=True)

    first_sys, dual, window_source = load_windows(pids, args.treatment_lines)

    rows = []
    for _, r in exam.iterrows():
        pid = r["patient_sn"]
        d = "" if pd.isna(r["exam_date_day"]) else str(r["exam_date_day"])
        fs = first_sys.get(pid) or ""
        d_days = None
        ts_d = to_ts(d) if len(d) == 10 else None
        ts_f = to_ts(fs) if len(fs) == 10 else None
        if ts_d is not None and ts_f is not None:
            d_days = (ts_d - ts_f).days
        in_dual = ""
        dual_days = None
        if pid in dual and d:
            s, e = dual[pid]
            if len(d) == 10:
                if d >= s and (e is None or d <= e):
                    in_dual = "open" if e is None else "closed"
                    dual_days = (pd.Timestamp(d) - pd.Timestamp(s)).days
                elif d < s:
                    in_dual = "before_dual"
                else:
                    in_dual = "after_dual"
        rows.append(
            {
                **{k: r[k] for k in ["patient_sn", "exam_date", "exam_date_day", "exam_type", "exam_site", "exam_name_origin", "exam_id", "exam_text", "exam_text_sha1"]},
                "days_from_first_systemic": d_days,
                "in_dual_window": in_dual,
                "days_from_dual_start": dual_days,
            }
        )
    org = pd.DataFrame(rows)
    org["exam_date_clean"] = org["exam_date_day"].replace("", pd.NA)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    org.drop(columns=[]).to_csv(out / "imaging_exam_organized.csv", index=False, encoding="utf-8-sig")

    cov = (
        org.groupby("patient_sn")
        .agg(
            n_exams=("exam_text", "size"),
            n_in_dual_open=("in_dual_window", lambda s: (s == "open").sum()),
            n_in_dual_closed=("in_dual_window", lambda s: (s == "closed").sum()),
            first_exam=("exam_date_clean", lambda s: s.dropna().min() if s.notna().any() else None),
            last_exam=("exam_date_clean", lambda s: s.dropna().max() if s.notna().any() else None),
        )
        .reset_index()
    )
    cov.to_csv(out / "patient_exam_coverage.csv", index=False, encoding="utf-8-sig")

    manifest = {
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "cohort_patients": len(pids),
        "sources": {
            k: {"path": str(FULL / "hzq_original" / f"基线数据.HSK-检查-all_{k}检查_gbk.csv"),
                "sha256": sha256_of(FULL / "hzq_original" / f"基线数据.HSK-检查-all_{k}检查_gbk.csv")}
            for k in ("CT", "MRI")
        },
        "rows_raw": before,
        "rows_deduped": len(org),
        "patients_with_exams": int(org["patient_sn"].nunique()),
        "date_parse_rate": round(float(org["exam_date_day"].notna().mean()), 4),
        "in_dual_window_open": int((org["in_dual_window"] == "open").sum()),
        "in_dual_window_closed": int((org["in_dual_window"] == "closed").sum()),
        "window_source": window_source,
        **(
            {
                "provisional_caveat": "双靶结束日期大多缺失（开口窗）；待新管线 step1 全量治疗方案行产出后用 --treatment-lines 重跑得闭口窗"
            }
            if window_source.startswith("provisional")
            else {}
        ),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"\nwritten -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
