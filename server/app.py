#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「声文」Web 服务。

启动：
  .venv/bin/python -u server/app.py --port 8765
  （或用产品根目录的 start.command 双击启动）
"""

import argparse
import os
import sys
import uuid
from pathlib import Path

SERVER_DIR = Path(__file__).parent.resolve()
APP_ROOT = SERVER_DIR.parent.resolve()
sys.path.insert(0, str(SERVER_DIR))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from jobs import JobManager

app = FastAPI(title="声文 · 录音转写")
manager = JobManager()

UPLOAD_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2GB
ALLOWED_EXT = {".mp3", ".mp4", ".m4a", ".wav", ".flac", ".aac", ".ogg",
               ".wma", ".avi", ".mov", ".mkv", ".webm", ".opus"}


@app.get("/", response_class=HTMLResponse)
def index():
    return (APP_ROOT / "web" / "index.html").read_text(encoding="utf-8")


@app.get("/favicon.svg")
def favicon():
    return FileResponse(APP_ROOT / "web" / "favicon.svg", media_type="image/svg+xml")


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    polish: bool = Form(False),
    prompt: str = Form(""),
    domain: str = Form("general"),
):
    orig_name = file.filename or "unnamed"
    ext = Path(orig_name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"不支持的文件格式 {ext}；支持 {sorted(ALLOWED_EXT)}")
    # 服务端随机名落盘（防同名竞态/路径注入），原始文件名只作展示
    import tempfile
    tmp = Path(tempfile.gettempdir()) / f"upload_{uuid.uuid4().hex}{ext}"
    total = 0
    try:
        with open(tmp, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > UPLOAD_MAX_BYTES:
                    raise HTTPException(413, "文件超过 2GB 上限")
                f.write(chunk)
    except HTTPException:
        tmp.unlink(missing_ok=True)
        raise
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    if domain not in ("religion", "meeting", "lecture", "general"):
        domain = "general"
    job = manager.create(tmp, polish=polish, prompt=prompt or None,
                         display_name=orig_name, domain=domain)
    return JSONResponse(job.to_dict())


@app.get("/api/jobs")
def list_jobs():
    return [j.to_dict() for j in manager.list()]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job.to_dict()


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    manager.delete(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/download/{kind}")
def download(job_id: str, kind: str):
    if kind not in ("txt", "srt", "docx", "json"):
        raise HTTPException(400, "kind 必须是 txt/srt/docx/json")
    p = manager.download_path(job_id, kind)
    if not p:
        raise HTTPException(404, "文件不存在或任务未完成")
    return FileResponse(p, filename=p.name)


@app.get("/api/jobs/{job_id}/speakers")
def get_speakers(job_id: str):
    """列出任务的说话人统计（供合并面板用）。"""
    job = manager.get(job_id)
    if not job or job.status != "done":
        raise HTTPException(404, "任务不存在或未完成")
    json_path = job.dir / "result.json"
    if not json_path.exists():
        raise HTTPException(404, "结果文件不存在")
    import json as _json
    from collections import Counter
    data = _json.loads(json_path.read_text())
    spk_stats = Counter()
    spk_chars = Counter()
    for s in data["sentences"]:
        spk_stats[s["spk_display"]] += 1
        spk_chars[s["spk_display"]] += len(s["text"])
    names = data.get("speaker_names", {})
    return {
        "speakers": [
            {"id": sid, "n_sent": spk_stats[sid], "n_chars": spk_chars[sid],
             "name": names.get(str(sid), "")}
            for sid in sorted(spk_stats)
        ]
    }


@app.post("/api/jobs/{job_id}/merge")
def merge_speakers(job_id: str, payload: dict):
    """合并/命名说话人：payload = {"targets": [2,5,7], "keep": 2, "name": "张三"(可选)}
    把 targets 里的所有说话人改成 keep；name 提供时给 keep 命名。重写全部结果文件。"""
    job = manager.get(job_id)
    if not job or job.status != "done":
        raise HTTPException(404, "任务不存在或未完成")
    targets = payload.get("targets") or []
    keep = payload.get("keep")
    name = (payload.get("name") or "").strip()
    if not targets or keep is None:
        raise HTTPException(400, "需要 targets（要合并的说话人列表）和 keep（保留的说话人）")
    # 强制整数类型（防类型注入进结果数据）
    try:
        keep = int(keep)
        targets = sorted({int(t) for t in targets})
    except (TypeError, ValueError):
        raise HTTPException(400, "targets/keep 必须是整数")
    if keep not in targets:
        targets = list(set(targets + [keep]))

    import json as _json
    from collections import Counter
    json_path = job.dir / "result.json"
    if not json_path.exists():
        raise HTTPException(404, "结果文件不存在")
    data = _json.loads(json_path.read_text())

    rename = {t: keep for t in targets}
    for s in data["sentences"]:
        if s["spk_display"] in rename:
            s["spk_display"] = keep
    # 命名（同时清理被合并编号的旧名字）
    if name:
        data.setdefault("speaker_names", {})[str(keep)] = name
    names = data.get("speaker_names", {})
    for t in targets:
        if t != keep and str(t) in names:
            del names[str(t)]
    # 重算 stats
    spk_stats = Counter(s["spk_display"] for s in data["sentences"])
    data["stats"]["n_speakers"] = len(spk_stats)
    json_path.write_text(_json.dumps(data, ensure_ascii=False, indent=2))

    # 重写 txt / srt / docx
    from fmt_utils import regenerate_outputs
    regenerate_outputs(job.dir, data)

    # polished 版本同步（如存在）
    polished_json = job.dir / "result_polished.json"
    if polished_json.exists():
        pdata = _json.loads(polished_json.read_text())
        for s in pdata["sentences"]:
            if s["spk_display"] in rename:
                s["spk_display"] = keep
        if name:
            pdata.setdefault("speaker_names", {})[str(keep)] = name
        for t in targets:
            if t != keep and str(t) in pdata.get("speaker_names", {}):
                del pdata["speaker_names"][str(t)]
        pstats = Counter(s["spk_display"] for s in pdata["sentences"])
        pdata["stats"]["n_speakers"] = len(pstats)
        polished_json.write_text(_json.dumps(pdata, ensure_ascii=False, indent=2))
        import fmt_utils
        fmt_utils._write_docx(job.dir / "result_polished.docx", pdata)
        pnames = pdata.get("speaker_names", {})
        with open(job.dir / "result_polished.txt", "w", encoding="utf-8") as f:
            for s in pdata["sentences"]:
                nm = pnames.get(str(s["spk_display"]))
                label = nm if nm else f"讲话人{s['spk_display']}"
                f.write(f"{label}  {fmt_utils.fmt_mmss(s['start'])}  {s['text']}\n")

    return {"ok": True, "n_speakers": len(spk_stats)}


# ---------------- 热词管理与校对学习 ----------------

@app.get("/api/hotwords")
def get_hotwords(domain: str = "general"):
    from hotwords_store import read_hotwords
    return read_hotwords(domain)


@app.post("/api/hotwords")
def add_hotword_api(payload: dict):
    from hotwords_store import add_hotword, DOMAINS
    domain = payload.get("domain", "general")
    if domain not in DOMAINS:
        raise HTTPException(400, f"domain 必须是 {DOMAINS}")
    wrong = (payload.get("wrong") or "").strip()
    right = (payload.get("right") or "").strip()
    if not wrong or not right:
        raise HTTPException(400, "wrong 和 right 都不能为空")
    if len(wrong) > 30 or len(right) > 30:
        raise HTTPException(400, "词条过长（≤30 字）")
    if any(ch in wrong + right for ch in ("=>", "\n", "\t", "#")):
        raise HTTPException(400, "词条不能包含 =>、换行、制表符或 # 字符")
    created = add_hotword(domain, wrong, right)
    return {"ok": True, "created": created}


@app.delete("/api/hotwords")
def remove_hotword_api(payload: dict):
    from hotwords_store import remove_hotword
    domain = payload.get("domain", "general")
    wrong = (payload.get("wrong") or "").strip()
    if not wrong:
        raise HTTPException(400, "需要 wrong")
    return {"ok": remove_hotword(domain, wrong)}


@app.post("/api/hotwords/import")
def import_hotwords_api(payload: dict):
    from hotwords_store import import_batch, DOMAINS
    domain = payload.get("domain", "general")
    if domain not in DOMAINS:
        raise HTTPException(400, f"domain 必须是 {DOMAINS}")
    text = payload.get("text") or ""
    added, updated = import_batch(domain, text)
    return {"ok": True, "added": added, "updated": updated}


@app.post("/api/jobs/{job_id}/learn")
async def learn_from_correction(job_id: str, file: UploadFile = File(...),
                                domain: str = Form("religion")):
    """校对学习：上传人工改好的 Word/txt，与原始转写 diff 出热词候选。"""
    job = manager.get(job_id)
    if not job or job.status != "done":
        raise HTTPException(404, "任务不存在或未完成")
    json_path = job.dir / "result.json"
    if not json_path.exists():
        raise HTTPException(404, "结果文件不存在")
    import json as _json
    data = _json.loads(json_path.read_text())

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".docx", ".txt"):
        raise HTTPException(400, "请上传 .docx 或 .txt（改好的校对稿）")
    import tempfile
    tmp = Path(tempfile.gettempdir()) / f"learn_{uuid.uuid4().hex}{suffix}"
    try:
        with open(tmp, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
        from hotwords_store import extract_replacements
        candidates = extract_replacements(data["sentences"], tmp)
    finally:
        tmp.unlink(missing_ok=True)

    return {"ok": True, "candidates": candidates, "domain": domain}


@app.post("/api/hotwords/confirm")
def confirm_candidates(payload: dict):
    """确认学习候选入库：payload = {domain, candidates: [{wrong, right}]}"""
    from hotwords_store import add_hotword, DOMAINS
    domain = payload.get("domain", "general")
    if domain not in DOMAINS:
        raise HTTPException(400, f"domain 必须是 {DOMAINS}")
    cands = payload.get("candidates") or []
    added = 0
    for c in cands:
        w = (c.get("wrong") or "").strip()
        r = (c.get("right") or "").strip()
        if w and r and len(w) <= 30 and len(r) <= 30:
            if add_hotword(domain, w, r):
                added += 1
    return {"ok": True, "added": added}


@app.post("/api/llm/models")
def list_llm_models(payload: dict):
    """拉取 LLM 服务商的可用模型列表（OpenAI 兼容 /models 端点）。
    payload = {api_base, api_key?}——key 缺省时用已保存配置。"""
    import json as _json
    import urllib.request
    import urllib.error

    from polish import load_config
    cfg = load_config()
    base = (payload.get("api_base") or cfg.get("llm_api_base") or "").strip().rstrip("/")
    key = (payload.get("api_key") or cfg.get("llm_api_key")
           or os.environ.get("LLM_API_KEY", "")).strip()
    if not base:
        raise HTTPException(400, "请先填写 API 地址")
    if not base.startswith("https://"):
        raise HTTPException(400, "API 地址必须以 https:// 开头")

    req = urllib.request.Request(
        f"{base}/models",
        headers={"Authorization": f"Bearer {key}"} if key else {},
        method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:150]
        raise HTTPException(502, f"服务商返回 {e.code}：{detail}")
    except Exception as e:
        raise HTTPException(502, f"无法连接 {base}：{e}")

    # OpenAI 兼容格式 {"data":[{"id":...}]}；兼容个别服务商直接返回列表
    items = data.get("data", data) if isinstance(data, dict) else data
    models = sorted({m.get("id") for m in items if isinstance(m, dict) and m.get("id")})
    return {"ok": True, "models": models}


@app.get("/api/settings")
def get_settings():
    from polish import load_config
    cfg = load_config()
    # 不回传完整 key，只回传掩码
    key = cfg.get("llm_api_key", "")
    cfg = dict(cfg)
    cfg["llm_api_key_masked"] = (key[:6] + "..." + key[-4:]) if len(key) > 12 else ("已设置" if key else "")
    cfg.pop("llm_api_key", None)
    return cfg


@app.post("/api/settings")
def save_settings(cfg: dict):
    from polish import load_config, save_config
    merged = load_config()
    for k in ("llm_api_key", "llm_api_base", "llm_model", "domain"):
        if k in cfg and cfg[k] is not None:
            # key 传空串表示清除
            merged[k] = cfg[k]
    # 校验 API 地址：必须 https（防 key 被发往任意 http 地址）
    base = (merged.get("llm_api_base") or "").strip()
    if base and not base.startswith("https://"):
        raise HTTPException(400, "API 地址必须以 https:// 开头")
    save_config(merged)
    return {"ok": True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", dest="open_browser", action="store_true", default=False)
    args = ap.parse_args()

    import uvicorn
    if args.open_browser:
        import threading, webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
