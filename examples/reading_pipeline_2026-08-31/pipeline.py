#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reading_pipeline：从病史 conclusion 文本提取全链条信息的可复跑管线（dprlab-reading 范例实现）。

本目录是 BRAF n316 项目管线的工作副本（源谱系 work/reading_pipeline_2026-08-31/，2026-08-31 完成，
2026-09-23 提升入 scripts/ 并通用化 --input）。设计要点（针对旧 run 缺陷）：
  - 不做 8000 字截断/关键词压缩：长文本按 4500 字窗口 + 250 字重叠切块，逐块提取
  - 每条事件强制 evidence_verbatim，merge 层校验"逐字原文子串"，未命中即标记
  - 输出仅限 JSON；建议/计划一律不抽（执行 vs 建议规则写入提示词）
  - 药典含拼写变体（维莫菲尼/维罗非尼/西妥西单抗），双靶组合判定在 merge 层用词表复算
  - 断点续跑：按 (step, patient_sn, chunk_idx) 键 checkpoint，重复运行自动跳过已完成

输入 CSV 只需两列：patient_sn（唯一患者标识）、conclusion（原文文本）。默认路径是 BRAF n316
的 curated reading_df（仅该项目机器上存在）；其他数据请用 --input 指向自己的 CSV。

用法（任意目录均可，路径按实际位置调整）：
  python scripts/reading_pipeline_2026-08-31/pipeline.py estimate --input <你的.csv>
  python scripts/reading_pipeline_2026-08-31/pipeline.py prepare --input <你的.csv> --stamp myrun
  python scripts/reading_pipeline_2026-08-31/pipeline.py run --stamp myrun --dry-run --limit 3   # 不联网，验证 prompt
  RP_BASE_URL=http://127.0.0.1:18000/v1 RP_MODEL=clinical-qwen36-35b RP_API_KEY=local-vllm \
      python scripts/reading_pipeline_2026-08-31/pipeline.py run --stamp myrun --steps 1,2,3,4 \
      --workers 8 --qwen-no-think
  python scripts/reading_pipeline_2026-08-31/pipeline.py merge --input <你的.csv> --stamp myrun

环境变量：RP_BASE_URL（默认 http://127.0.0.1:8000/v1）、RP_API_KEY、RP_MODEL（必填，run 时）。
输出：<本目录>/runs/<stamp>/（batches/、responses/、merged/、qc_report.md；患者级数据，永不入 Git）。
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime as dt
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
READING_CSV = REPO / "data/processed/2026-08-30_spark_extraction_curated_v1/2026-08-30_01_reading_df_source_text_v1.csv"

STEPS = {
    1: "step1_systemic_lines",
    2: "step2_response_evals",
    3: "step3_metastasis_progression",
    4: "step4_local_treatment_ned",
}
WINDOW = 4500
OVERLAP = 250
TAIL_CHARS = 200
MAX_TOKENS = 1500
TEMPERATURE = 0.0
TOKENS_PER_CHAR = 0.7      # 中文在 Qwen/GLM 系分词器的经验值区间 0.6–1.0
TEMPLATE_IN_TOKENS = 1000  # 系统提示+规则+药典 的保守开销


def load_texts(csv_path: str | Path | None = None) -> pd.DataFrame:
    p = Path(csv_path) if csv_path else READING_CSV
    if not p.exists():
        sys.exit(
            f"输入 CSV 不存在：{p}\n"
            "  - BRAF n316 默认路径仅在该项目机器上有效；\n"
            "  - 用自己的数据时请传 --input <含 patient_sn 和 conclusion 两列的 CSV>"
        )
    df = pd.read_csv(p)
    missing = [c for c in ("patient_sn", "conclusion") if c not in df.columns]
    if missing:
        sys.exit(f"输入 CSV 缺少必需列 {missing}（需要 patient_sn + conclusion）: {p}")
    df["patient_sn"] = df["patient_sn"].astype(str)
    df["conclusion"] = df["conclusion"].astype(str)
    return df[["patient_sn", "conclusion"]]


def chunk_count(length: int, window: int = WINDOW, overlap: int = OVERLAP) -> int:
    if length <= window:
        return 1
    import math

    step = window - overlap
    return 1 + math.ceil((length - window) / step)


def chunk_text(text: str, window: int = WINDOW, overlap: int = OVERLAP) -> list[str]:
    """定长滑窗切块；与 chunk_count 严格一致，不产生碎尾块。"""
    if len(text) <= window:
        return [text]
    step = window - overlap
    chunks = []
    i = 0
    while i < len(text):
        if i + window >= len(text):
            chunks.append(text[i:])
            break
        chunks.append(text[i : i + window])
        i += step
    return chunks


def render_prompt(step: int, patient_sn: str, idx: int, n: int, chunk: str, tail: str) -> str:
    from lexicon import LEXICON_LINES

    tpl = (HERE / "prompts" / f"{STEPS[step]}.md").read_text(encoding="utf-8")
    return tpl.format(
        lexicon=LEXICON_LINES,
        patient_sn=patient_sn,
        chunk_idx=idx,
        n_chunks=n,
        chunk=chunk,
        tail=tail[-TAIL_CHARS:] if idx > 1 else "（无，本块为第一块）",
    )


def run_dir(stamp: str) -> Path:
    d = HERE / "runs" / stamp
    (d / "batches").mkdir(parents=True, exist_ok=True)
    (d / "responses").mkdir(exist_ok=True)
    (d / "merged").mkdir(exist_ok=True)
    return d


# ----------------------------------------------------------------- estimate

def cmd_estimate(args: argparse.Namespace) -> None:
    df = load_texts(args.input)
    lens = df["conclusion"].str.len()
    nch = lens.apply(chunk_count)
    calls_total = int(nch.sum()) * len(STEPS)
    in_lo = in_hi = 0
    for L, c in zip(lens, nch):
        c = int(c)
        chars = min(L, WINDOW) + (c - 1) * (WINDOW - OVERLAP)
        # 每个 step 都会重发同一文本块：输入按步数放大
        in_lo += (int(chars * 0.6) + TEMPLATE_IN_TOKENS * c) * len(STEPS)
        in_hi += (int(chars * 1.0) + TEMPLATE_IN_TOKENS * c) * len(STEPS)
    out_lo = int(calls_total * 400)
    out_hi = int(calls_total * MAX_TOKENS)
    print(f"patients={len(df)}  total_chars={int(lens.sum()):,}  chunks/patient mean={nch.mean():.2f} max={int(nch.max())}")
    print(f"steps={len(STEPS)}  total_calls={calls_total}")
    print(f"INPUT  tokens ≈ {in_lo/1e6:.1f} – {in_hi/1e6:.1f} M   (0.6–1.0 token/字 + 模板开销)")
    print(f"OUTPUT tokens ≈ {out_lo/1e6:.1f} – {out_hi/1e6:.1f} M   (实际输出通常 300–800/次)")


# ----------------------------------------------------------------- prepare

def cmd_prepare(args: argparse.Namespace) -> None:
    df = load_texts(args.input)
    d = run_dir(args.stamp)
    count = 0
    for step in STEPS:
        if args.steps and step not in args.steps:
            continue
        with (d / "batches" / f"step{step}.jsonl").open("w", encoding="utf-8") as f:
            for _, row in df.iterrows():
                pid, text = row["patient_sn"], row["conclusion"]
                chunks = chunk_text(text)
                for i, ch in enumerate(chunks, 1):
                    prompt = render_prompt(step, pid, i, len(chunks), ch, text[max(0, (i - 1) * (WINDOW - OVERLAP)): (i - 1) * (WINDOW - OVERLAP) + OVERLAP] if i > 1 else "")
                    rec = {
                        "key": f"s{step}|{pid}|{i:02d}",
                        "step": step,
                        "patient_sn": pid,
                        "chunk_idx": i,
                        "n_chunks": len(chunks),
                        "prompt": prompt,
                        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    count += 1
    print(f"prepared {count} calls -> {d/'batches'} (stamp={args.stamp})")


# ----------------------------------------------------------------- run

BACKENDS = {
    # OpenAI 兼容端点；GLM v4 端点直接接受 Bearer API Key
    "glm": "https://open.bigmodel.cn/api/paas/v4",
    "openai": "",
}


def call_openai(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: int = 300,
    extra_payload: dict | None = None,
    max_tokens: int = MAX_TOKENS,
) -> tuple[str, dict]:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": TEMPERATURE,
        "max_tokens": max_tokens,
    }
    if extra_payload:
        body.update(extra_payload)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key or 'EMPTY'}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    usage = payload.get("usage", {})
    return payload["choices"][0]["message"]["content"], usage


def cmd_run(args: argparse.Namespace) -> None:
    d = run_dir(args.stamp)
    # 配置优先级：CLI 参数 > 环境变量 > config_gui.py 保存的 .api_config.json
    cfg_path = HERE / ".api_config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cfg = {}
    model = args.model or os.environ.get("RP_MODEL") or str(cfg.get("model", ""))
    base_url = args.base_url or os.environ.get("RP_BASE_URL") or str(cfg.get("base_url", ""))
    if not base_url:
        base_url = BACKENDS["glm"] if args.backend == "glm" else "http://127.0.0.1:8000/v1"
    api_key = os.environ.get("RP_API_KEY") or str(cfg.get("api_key", ""))
    extra_payload = None
    if args.backend == "glm":
        # GLM-5.x-Flash 等型号始终思考、不支持 disabled（报错 1210），默认 low 档
        if args.thinking_level == "off":
            extra_payload = {"thinking": {"type": "disabled"}}
        else:
            extra_payload = {"thinking": {"type": "enabled", "level": args.thinking_level}}
    if args.qwen_no_think:
        # vLLM + Qwen3/3.5：chat 模板层关闭思考（经服务器实测有效）
        extra_payload = dict(extra_payload or {}, **{"chat_template_kwargs": {"enable_thinking": False}})

    tasks = []
    for step in STEPS:
        if args.steps and step not in args.steps:
            continue
        bp = d / "batches" / f"step{step}.jsonl"
        if not bp.exists():
            print(f"[warn] batch file missing, skipping step {step}: {bp}")
            continue
        tasks += [json.loads(l) for l in bp.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not tasks:
        sys.exit("no tasks found (run prepare first, or fix --steps)")
    if args.limit:
        tasks = tasks[: args.limit]

    done = set()
    out_path = d / "responses" / "responses.jsonl"
    if out_path.exists():
        for l in out_path.read_text(encoding="utf-8").splitlines():
            if l.strip():
                try:
                    rec = json.loads(l)
                except json.JSONDecodeError:
                    continue  # 历史损坏行：视为未完成，重跑自动补
                if rec.get("ok") and (rec.get("response") or "").strip():
                    done.add(rec["key"])  # 失败/空正文不算完成，重跑时自动补
    tasks = [t for t in tasks if t["key"] not in done]
    print(f"run: {len(tasks)} calls to make ({len(done)} already done) -> {out_path}")

    if args.dry_run:
        for t in tasks[:3]:
            print("=" * 60)
            print(t["prompt"][:600], "…")
        print(f"[dry-run] OK, {len(tasks)} prompts rendered, no network calls.")
        return
    if not model:
        sys.exit("model 未配置：设置 RP_MODEL 环境变量或 --model 参数（spark 生产模型名为 clinical-qwen36-35b）")

    lock_write = threading.Lock()  # Windows 下多线程 append 可能交错，必须加锁

    def work(t):
        last_err = ""
        for attempt in range(args.retries + 1):
            try:
                content, usage = call_openai(
                    base_url, api_key, model, t["prompt"],
                    extra_payload=extra_payload, max_tokens=args.max_tokens,
                )
                ok, err = True, ""
                if not (content or "").strip():
                    # 思考型模型可能把 max_tokens 全花在 reasoning 上导致正文为空
                    ok, err = False, f"empty content (reasoning exhausted max_tokens?): usage={json.dumps(usage, ensure_ascii=False)[:200]}"
            except urllib.error.HTTPError as e:
                content, usage, ok, err = "", {}, False, f"HTTP {e.code}: {e.read()[:200]}"
                if e.code < 500 and e.code != 429:
                    break  # 非 5xx/429 的客户端错误重试无意义
            except Exception as e:  # noqa: BLE001
                content, usage, ok, err = "", {}, False, repr(e)
            if ok:
                last_err = ""
                break
            last_err = err
            if attempt < args.retries:
                time.sleep(min(60, 10 * (attempt + 1)))
        else:
            err = last_err
        rec = {
            "key": t["key"],
            "step": t["step"],
            "patient_sn": t["patient_sn"],
            "chunk_idx": t["chunk_idx"],
            "model": model,
            "prompt_sha256": t["prompt_sha256"],
            "usage": usage,
            "ok": ok,
            "error": err,
            "response": content,
        }
        rec_json = json.dumps(rec, ensure_ascii=False)
        with lock_write:
            with out_path.open("a", encoding="utf-8") as f:
                f.write(rec_json + "\n")
        return t["key"], ok

    fails = 0
    with futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for key, ok in ex.map(work, tasks):
            if not ok:
                fails += 1
            print(("OK  " if ok else "FAIL"), key, flush=True)
    print(f"done: {len(tasks) - fails}/{len(tasks)} ok, {fails} failed; rerun same command to retry failures.")


# ----------------------------------------------------------------- merge

DATE_RE = re.compile(r"^(\d{4})[-/年.](\d{1,2})?[-/月.]?(\d{1,2})?[日]?$")


def norm_date(s: str) -> str:
    s = str(s or "").strip()
    if not s:
        return ""
    s = s.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$", s)
    if not m:
        return ""
    y, mo, dd = m.group(1), m.group(2), m.group(3)
    if not mo:
        return y
    if not dd:
        return f"{y}-{int(mo):02d}"
    return f"{y}-{int(mo):02d}-{int(dd):02d}"


def parse_json_loose(content: str) -> dict:
    content = content.strip()
    content = re.sub(r"^```(json)?|```$", "", content, flags=re.M).strip()
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object found")
    blob = content[start : end + 1]
    blob = re.sub(r",\s*([}\]])", r"\1", blob)  # LLM 常见尾逗号
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # 兜底：值内未转义引号等 LLM 畸形 JSON
        try:
            from json_repair import repair_json

            return json.loads(repair_json(blob))
        except ImportError:
            raise


def cmd_merge(args: argparse.Namespace) -> None:
    from lexicon import CLASS_OF, is_dual_combo, scan_drugs

    d = HERE / "runs" / args.stamp
    src = d / "responses" / "responses.jsonl"
    if not src.exists():
        sys.exit(f"no responses file: {src}")
    texts = load_texts(args.input).set_index("patient_sn")["conclusion"].to_dict()

    rows = {"treatment_lines": [], "response_evaluations": [], "metastasis_events": [], "progression_events": [], "local_treatments": [], "ned_statements": []}
    ev_hit = ev_total = 0
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # 并发写交错产生的损坏行：跳过（checkpoint 会补跑这些键）
        if not rec.get("ok"):
            continue
        pid = rec["patient_sn"]
        source_text = texts.get(pid, "")
        try:
            payload = parse_json_loose(rec["response"])
        except Exception as e:  # noqa: BLE001
            print(f"[warn] unparsable response {rec['key']}: {e}")
            continue
        for list_key, items in payload.items():
            if list_key not in rows:
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                it = dict(it)
                it["patient_sn"] = pid
                ev = str(it.get("evidence_verbatim", "")).strip()
                if ev:
                    ev_total += 1
                    ev_clean = re.sub(r"\s+", "", ev)
                    src_clean = re.sub(r"\s+", "", source_text)
                    hit = bool(ev_clean) and ev_clean[:80] in src_clean
                    ev_hit += int(hit)
                    it["evidence_hit"] = hit
                if list_key == "treatment_lines":
                    from lexicon import normalize_drugs

                    # 药物必须在方案原文或证据句中可验证，模型幻觉药名一律剔除
                    supporting = str(it.get("regimen_raw", "")) + " " + str(it.get("evidence_verbatim", ""))
                    drugs = normalize_drugs(
                        it.get("drugs_standardized", []), supporting
                    )
                    it["drugs_standardized"] = drugs
                    it["dual_combo_lexicon"] = is_dual_combo(drugs)
                for dk in ("start_date", "end_date", "date"):
                    if dk in it:
                        it[dk] = norm_date(it[dk])
                rows[list_key].append(it)

    # cross-chunk dedup: same patient + key fields
    def dedup(items, keys):
        seen, out = set(), []
        for it in items:
            k = tuple(str(it.get(x, "")) for x in keys) + (it["patient_sn"],)
            if k in seen:
                continue
            seen.add(k)
            out.append(it)
        return out

    tl = dedup([r for r in rows["treatment_lines"] if str(r.get("intent", "执行")) != "建议"], ["regimen_raw", "start_date"])
    tl = dedup(tl, ["regimen_raw", "cycles"])
    re_ev = dedup(rows["response_evaluations"], ["date", "result", "evidence_verbatim"])
    me = dedup(rows["metastasis_events"], ["date", "type", "evidence_verbatim"])
    pe = dedup(rows["progression_events"], ["date", "evidence_verbatim"])
    lt = dedup(rows["local_treatments"], ["date", "name_raw"])
    ned = dedup(rows["ned_statements"], ["date", "status"])

    # per-patient dual summary (executed dual lines only)
    dual = {}
    for r in tl:
        if not r.get("dual_combo_lexicon"):
            continue
        p = r["patient_sn"]
        s, e = r.get("start_date", ""), r.get("end_date", "")
        cur = dual.setdefault(p, {"has_dual": True, "first_dual_start": "", "last_dual_end": "", "end_reasons": [], "n_dual_lines": 0})
        cur["n_dual_lines"] += 1
        if s and (not cur["first_dual_start"] or s < cur["first_dual_start"]):
            cur["first_dual_start"] = s
        if e and e > cur["last_dual_end"]:
            cur["last_dual_end"] = e
    wide = pd.DataFrame(
        [
            {
                "patient_sn": p,
                "has_dual": p in dual,
                "first_dual_start": dual.get(p, {}).get("first_dual_start", ""),
                "last_dual_end": dual.get(p, {}).get("last_dual_end", ""),
                "n_dual_lines": dual.get(p, {}).get("n_dual_lines", 0),
                "n_treatment_lines": sum(1 for r in tl if r["patient_sn"] == p),
                "best_response": next((r["result"] for r in sorted(re_ev, key=lambda x: x.get("date", "")) if r["patient_sn"] == p and r.get("result") in ("CR", "PR")), next((r["result"] for r in sorted(re_ev, key=lambda x: x.get("date", "")) if r["patient_sn"] == p and r.get("result") == "SD"), "")),
                "ned_ever": any(r["patient_sn"] == p and r.get("status") == "NED" for r in ned),
            }
            for p in texts
        ]
    )

    out = d / "merged"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(tl).to_csv(out / "treatment_lines.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(re_ev).to_csv(out / "response_evaluations.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(me).to_csv(out / "metastasis_events.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(pe).to_csv(out / "progression_events.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(lt).to_csv(out / "local_treatments.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(ned).to_csv(out / "ned_statements.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(out / "patient_wide_fact.csv", index=False, encoding="utf-8-sig")

    qc = [
        "# QC report",
        f"- responses parsed from: {src}",
        f"- treatment lines (executed, deduped): {len(tl)}",
        f"- dual-combo lines (lexicon flag): {sum(1 for r in tl if r.get('dual_combo_lexicon'))}",
        f"- evidence_verbatim present: {ev_total}; substring-verified: {ev_hit} ({(ev_hit/ev_total*100 if ev_total else 0):.1f}%)",
        f"- patients flagged dual by lexicon: {len(dual)}",
        "",
        "注意：dual_combo_lexicon 是词表机器标记；最终双靶判定必须人工复核 evidence_verbatim（联合 vs 序贯、执行 vs 建议、癌种导向）。",
    ]
    (d / "qc_report.md").write_text("\n".join(qc), encoding="utf-8")
    print("\n".join(qc))
    print(f"\nmerged -> {out}")


# ----------------------------------------------------------------- cli

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("estimate", help="token/调用量估算")
    p.add_argument("--stamp", default="estimate")
    p.add_argument("--input", default=None, help="输入 CSV（patient_sn+conclusion 两列；默认 BRAF n316 curated 路径）")
    p.set_defaults(fn=cmd_estimate)

    p = sub.add_parser("prepare", help="切块并渲染全部 prompt")
    p.add_argument("--stamp", default=dt.datetime.now().strftime("%Y%m%d_%H%M"))
    p.add_argument("--steps", type=lambda s: {int(x) for x in s.split(",")}, default=None)
    p.add_argument("--input", default=None, help="输入 CSV（patient_sn+conclusion 两列；默认 BRAF n316 curated 路径）")
    p.set_defaults(fn=cmd_prepare)

    p = sub.add_parser("run", help="调用后端执行提取（断点续跑）")
    p.add_argument("--stamp", default=dt.datetime.now().strftime("%Y%m%d_%H%M"))
    p.add_argument("--steps", type=lambda s: {int(x) for x in s.split(",")}, default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--backend", choices=list(BACKENDS), default="openai", help="glm=智谱BigModel API；openai=自定义OpenAI兼容端点(vLLM等)")
    p.add_argument("--base-url", default=None, help="覆盖后端默认端点")
    p.add_argument("--model", default=None, help="模型名（也可用 RP_MODEL 环境变量）")
    p.add_argument("--thinking-level", choices=["off", "low", "high", "max"], default="low",
                   help="GLM 后端思考档位；Flash 型号始终思考不支持 off（默认 low）")
    p.add_argument("--max-tokens", type=int, default=MAX_TOKENS,
                   help="单次输出上限；思考型模型建议 3000+（思考也计入）")
    p.add_argument("--qwen-no-think", action="store_true",
                   help="vLLM+Qwen3/3.5：chat_template_kwargs.enable_thinking=false")
    p.add_argument("--retries", type=int, default=3,
                   help="每次调用失败（含连接错误）的自动重试次数，退避 10/20/30 秒")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("merge", help="解析响应、校验证据、生成患者级表格与 QC 报告")
    p.add_argument("--stamp", default=dt.datetime.now().strftime("%Y%m%d_%H%M"))
    p.add_argument("--input", default=None, help="输入 CSV（须与 prepare 用同一份；merge 用原文校验 evidence_verbatim）")
    p.set_defaults(fn=cmd_merge)

    args = ap.parse_args()
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
