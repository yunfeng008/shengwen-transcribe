#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转写流水线：上传音频 → 16k wav → 分段 MOSS 推理 → 说话人对齐 → 输出。

被 jobs.py 的 worker 线程调用，也可命令行独立使用：
  python pipeline.py <音频文件> <输出目录> [--spk-prompt "热词..."]
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

from segmenter import (plan_segments, cut_segment, align_and_merge,
                       SegmentCheckpoint, get_duration)
from moss_engine import MossEngine

# 短音频阈值：不超过就直接整段推理
SINGLE_PASS_MAX_SEC = 720  # 12 分钟
SEGMENT_SEC = 600          # 每段 10 分钟
OVERLAP_SEC = 30           # 段间重叠


def to_wav16k(src, dst):
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(dst),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _load_hotword_map(domain="general"):
    """读 assets/hotwords_<domain>.txt，返回 [(错误词, 正确词)]（保持文件顺序）。
    领域文件不存在时回退 general。
    另合并 runs/local_hotwords_<domain>.txt（个人热词：人名等私有修正，
    存于私有数据区不进仓库）。"""
    assets = Path(__file__).parent.parent / "assets"
    path = assets / f"hotwords_{domain}.txt"
    if not path.exists():
        path = assets / "hotwords_general.txt"

    pairs = []
    # 顺序即优先级：个人表在前（人名等私有修正优先于领域表），后出现的相同 wrong 不再覆盖
    # 替换按列表顺序单趟执行，相同 wrong 先匹配先赢
    for src in (Path(__file__).parent.parent / "runs" / f"local_hotwords_{domain}.txt", path):
        if not src.exists():
            continue
        for line in src.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=>" not in line:
                continue
            wrong, right = line.split("=>", 1)
            if wrong.strip() and right.strip():
                pairs.append((wrong.strip(), right.strip()))
    return pairs


def fmt_mmss(sec):
    sec = int(sec)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_srt(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600 * 1000)
    m, ms = divmod(ms, 60 * 1000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def run_pipeline(audio_src, job_dir, prompt=None, progress_cb=None, domain="general"):
    """完整流水线。

    audio_src: 上传的原始音频
    job_dir: 该任务的 run 目录（存中间件和结果）
    prompt: 可选 MOSS prompt 附加内容（热词）
    progress_cb: fn(stage: str, pct: float, message: str)

    返回 dict：{sentences, stats, files}
    """
    def report(stage, pct, msg=""):
        if progress_cb:
            progress_cb(stage, pct, msg)
        print(f"[{stage}] {pct:.0f}% {msg}", flush=True)

    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    wav = job_dir / "audio_16k.wav"
    seg_dir = job_dir / "segments"
    seg_dir.mkdir(exist_ok=True)

    # 1. 转 16k wav
    report("prepare", 2, "转换音频格式...")
    if not wav.exists():
        to_wav16k(audio_src, wav)
        # wav 已生成，删除任务目录里的上传副本省空间（原始文件只在 job_dir 内才删，
        # CLI 直传外部文件时不动用户源文件）
        src_path = Path(audio_src)
        try:
            if src_path.resolve().parent == job_dir.resolve():
                src_path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    duration = get_duration(wav)
    report("prepare", 5, f"音频 {fmt_mmss(duration)}，共 {duration:.0f} 秒")

    # 2. 切段计划
    plan = plan_segments(duration, SEGMENT_SEC, OVERLAP_SEC)
    n_seg = len(plan)

    # 3. 加载引擎 + 逐段推理
    engine = MossEngine.get()
    checkpoint = SegmentCheckpoint(job_dir)
    t0 = time.time()

    for (seg_start, seg_end, idx) in plan:
        if checkpoint.is_done(idx):
            continue
        seg_path = seg_dir / f"seg_{idx:04d}.wav"
        if not seg_path.exists():
            cut_segment(wav, seg_start, seg_end, seg_path)

        seg_base_pct = 5 + 85 * idx / n_seg
        seg_span_pct = 85 / n_seg
        report("transcribe", seg_base_pct,
               f"转写第 {idx+1}/{n_seg} 段（{fmt_mmss(seg_start)} 起）...")
        seg_t0 = time.time()

        # 段内进度：按生成 token 数估算（约 12.5 token/秒音频）
        # 回调每 token 触发，节流到每 2% 汇报一次
        seg_dur = seg_end - seg_start
        est_tokens = max(1, int(seg_dur * 12.5))
        last_pct = [seg_base_pct]

        def on_tokens(n_generated, _base=seg_base_pct, _span=seg_span_pct,
                      _est=est_tokens, _idx=idx, _n=n_seg, _last=last_pct):
            frac = min(n_generated / _est, 0.99)
            pct = _base + _span * frac
            if pct - _last[0] < 2:
                return
            _last[0] = pct
            report("transcribe", pct,
                   f"转写第 {_idx+1}/{_n} 段... {frac*100:.0f}%")

        segments, _raw = engine.transcribe(str(seg_path), prompt=prompt,
                                           progress_callback=on_tokens)
        checkpoint.mark_done(idx, segments)
        print(f"    段 {idx+1} 完成：{len(segments)} 句，"
              f"耗时 {time.time()-seg_t0:.0f}s", flush=True)

        # 清理该段 wav 省空间（checkpoint 里已有结果）
        seg_path.unlink(missing_ok=True)

    report("transcribe", 92, f"转写完成，开始对齐合并...")
    elapsed = time.time() - t0

    # 4. 对齐合并
    all_results = [checkpoint.get(idx) or [] for (_, _, idx) in plan]
    merged = align_and_merge(all_results, plan, OVERLAP_SEC)

    # 4.5 热词替换（assets/hotwords.txt 里的 错误词=>正确词，按文件顺序）
    hotword_map = _load_hotword_map(domain)
    if hotword_map:
        for s in merged:
            t = s["text"]
            for wrong, right in hotword_map:
                if wrong in t:
                    t = t.replace(wrong, right)
            s["text"] = t
        report("export", 94, f"已应用 {len(hotword_map)} 条热词修正")

    # 5. 输出
    report("export", 95, "生成输出文件...")
    stem = Path(audio_src).stem
    sentences = []
    for s in merged:
        try:
            spk_num = int(s["speaker"].lstrip("S"))
        except ValueError:
            spk_num = 1
        sentences.append({
            "start": round(s["start"], 2),
            "end": round(s["end"], 2),
            "speaker": s["speaker"],
            "spk_display": spk_num,
            "text": s["text"],
        })

    result = {
        "title": stem,
        "duration": duration,
        "sentences": sentences,
        "stats": {
            "n_segments": len(sentences),
            "n_speakers": len({s["speaker"] for s in sentences}),
            "n_chars": sum(len(s["text"]) for s in sentences),
            "audio_minutes": round(duration / 60, 1),
            "process_minutes": round(elapsed / 60, 1),
        },
    }

    # json
    json_path = job_dir / "result.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    # txt
    txt_path = job_dir / "result.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        for s in sentences:
            f.write(f"讲话人{s['spk_display']}  {fmt_mmss(s['start'])}  {s['text']}\n")
    # srt
    srt_path = job_dir / "result.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, s in enumerate(sentences, 1):
            f.write(f"{i}\n{fmt_srt(s['start'])} --> {fmt_srt(s['end'])}\n"
                    f"[讲话人{s['spk_display']}] {s['text']}\n\n")
    # docx
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn

        doc = Document()
        style = doc.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(11)
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

        h = doc.add_heading(stem, level=0)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        st = result["stats"]
        meta = doc.add_paragraph()
        meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = meta.add_run(
            f"时长 {fmt_mmss(duration)}　|　讲话人 {st['n_speakers']} 位　|　"
            f"共 {st['n_segments']} 句 / {st['n_chars']} 字　|　"
            f"{time.strftime('%Y-%m-%d')}")
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

        cur_spk, buf, t_start = None, [], None
        def flush():
            nonlocal cur_spk, buf, t_start
            if cur_spk is not None and buf:
                p = doc.add_paragraph()
                lab = p.add_run(f"讲话人{cur_spk}  [{fmt_mmss(t_start)}]  ")
                lab.bold = True
                lab.font.color.rgb = RGBColor(0x1a, 0x5c, 0xb0)
                p.add_run("".join(buf))
            cur_spk, buf, t_start = None, [], None
        for s in sentences:
            if s["spk_display"] != cur_spk:
                flush()
                cur_spk, t_start = s["spk_display"], s["start"]
            buf.append(s["text"])
        flush()
        docx_path = job_dir / "result.docx"
        doc.save(str(docx_path))
        files = {"json": json_path.name, "txt": txt_path.name,
                 "srt": srt_path.name, "docx": docx_path.name}
    except ImportError:
        files = {"json": json_path.name, "txt": txt_path.name, "srt": srt_path.name}

    st = result["stats"]
    # 任务完成：清理大文件（wav 已无用；checkpoint 保留备查但很小）
    wav.unlink(missing_ok=True)
    if seg_dir.exists() and not any(seg_dir.iterdir()):
        seg_dir.rmdir()

    report("done", 100, f"完成！{st['n_speakers']} 位讲话人，{st['n_segments']} 句")
    return result, files


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("job_dir")
    ap.add_argument("--prompt", default=None)
    args = ap.parse_args()
    result, files = run_pipeline(args.audio, args.job_dir, prompt=args.prompt)
    print(json.dumps(result["stats"], ensure_ascii=False, indent=2))
    print(json.dumps(files, ensure_ascii=False, indent=2))
