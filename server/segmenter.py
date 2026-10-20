#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长音频分段 + 跨段说话人对齐。

设计：
  - 把长 wav 切成 N 段（每段 segment_sec 秒，相邻段重叠 overlap_sec 秒）
  - 每段独立送 MOSS 推理，得到段内的 [S01/S02...] 说话人标签
  - 段间对齐：重叠区里两段都识别出了句子，按「时间重叠 + 文本相似度」
    建立映射（如 段1.S02 == 段2.S01），链式传递成全局说话人编号
  - 每段完成立即写 checkpoint，支持断点续传
"""

import json
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path


def get_duration(wav_path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(wav_path),
    ]).decode().strip()
    return float(out)


def plan_segments(duration, segment_sec=600, overlap_sec=30):
    """生成切段计划 [(start, end, seg_index)]，单位秒。

    相邻段重叠 overlap_sec，用于说话人对齐。
    """
    if duration <= segment_sec:
        return [(0.0, duration, 0)]
    segments = []
    start = 0.0
    idx = 0
    while start < duration - 1:
        end = min(start + segment_sec, duration)
        segments.append((start, end, idx))
        if end >= duration:
            break  # 已到最后一段（必须 break，否则剩余 ≤ overlap 时 start 不前进死循环）
        start = end - overlap_sec  # 下一段往回退重叠区
        idx += 1
    return segments


def cut_segment(wav_path, start, end, out_path):
    """ffmpeg 按 [start, end) 切一段 wav。"""
    dur = end - start
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
        "-i", str(wav_path),
        "-ac", "1", "-ar", "16000", "-f", "wav", str(out_path),
    ], check=True)


def _norm_text(s):
    """归一化文本用于相似度比较：去空白和标点。"""
    return "".join(c for c in s if c.isalnum())


def match_overlap_speakers(prev_segs, next_segs, prev_dur, overlap_sec=30.0,
                          min_text_len=3, min_score=0.30):
    """建立 prev 段 -> next 段的说话人映射。

    改进（针对长录音场景）：
      - 加大重叠区搜索窗口到 60 秒（MOSS 在 30 秒内可能识别不到有效句子）
      - 降低文本相似度门槛到 0.30（音频 + ASR 噪声下文本会差异较大）
      - 降低有效句子长度门槛到 3 字
      - 时间容差放宽到 30 秒

    返回 {prev_spk: next_spk}。
    """
    if not prev_segs or not next_segs:
        return {}

    # 重叠区：双向扩展（段尾 60s + 段头 60s）
    prev_overlap = [s for s in prev_segs if s["start"] >= prev_dur - 60]
    next_overlap = [s for s in next_segs if s["end"] <= 60 + 5]

    if not prev_overlap or not next_overlap:
        return {}

    prev_valid = [s for s in prev_overlap if len(_norm_text(s["text"])) >= min_text_len]
    next_valid = [s for s in next_overlap if len(_norm_text(s["text"])) >= min_text_len]

    if not prev_valid or not next_valid:
        return {}

    # 每对句子算综合分
    votes = {}  # (prev_spk, next_spk) -> score
    for p in prev_valid:
        best_pair, best_score = None, 0.0
        for n in next_valid:
            p_global = p["start"]
            n_global = n["start"] + (prev_dur - 30)  # 段间偏移
            time_diff = abs(p_global - n_global)
            if time_diff > 30:
                continue
            time_score = max(0.0, 1 - time_diff / 30)
            text_score = SequenceMatcher(
                None, _norm_text(p["text"]), _norm_text(n["text"])).ratio()
            score = text_score * 0.7 + time_score * 0.3
            if score > best_score:
                best_pair, best_score = (p["speaker"], n["speaker"]), score
        if best_pair and best_score >= min_score:
            votes[best_pair] = votes.get(best_pair, 0.0) + best_score

    if not votes:
        return {}

    # 每个 prev_spk 取得分最高的 next_spk（一对一）
    mapping = {}
    used_next = set()
    for (p_spk, n_spk), score in sorted(votes.items(), key=lambda x: -x[1]):
        if p_spk in mapping or n_spk in used_next:
            continue
        mapping[p_spk] = n_spk
        used_next.add(n_spk)
    return mapping


def align_and_merge(all_segment_results, segment_plan, overlap_sec=30.0):
    """把所有段的 segments 对齐合并成统一时间轴 + 全局说话人编号。

    all_segment_results: list of segments list（每段 MOSS 输出，时间基于段起点）
    segment_plan: plan_segments 的输出 [(start, end, idx)]

    返回 merged sentences：[{start,end,speaker(全局 S01..),text}] 按时间排序。
    """
    # 1. 链式建立每段 local->global 映射
    global_maps = []  # 每段 {local_spk: "SXX"}
    next_available = 1  # 下一个可分配的全局编号（S01 开始）
    prev_raw_map = None  # 上一段的 (mapping_to_global, local_ids)

    for seg_idx, segs in enumerate(all_segment_results):
        local_spks = []
        for s in segs:
            if s["speaker"] not in local_spks:
                local_spks.append(s["speaker"])

        if seg_idx == 0:
            # 第一段：按出现顺序分配全局编号
            m = {spk: f"S{i+1:02d}" for i, spk in enumerate(local_spks)}
            global_maps.append(m)
            next_available = len(local_spks) + 1
            prev_raw_map = (m, local_spks)
            continue

        # 后续段：用重叠区匹配建立 local(prev_seg) -> local(cur_seg)
        prev_start, prev_end, _ = segment_plan[seg_idx - 1]
        prev_segs = all_segment_results[seg_idx - 1]
        cross_map = match_overlap_speakers(
            prev_segs, segs, prev_end - prev_start, overlap_sec)

        prev_to_global, prev_locals = prev_raw_map
        # cross_map 方向是 {prev_spk: next_spk}，反查表让 next 的说话人找到 prev 的
        rev_cross = {v: k for k, v in cross_map.items()}
        cur_map = {}
        for spk in local_spks:  # spk 是当前（next）段的说话人
            if spk in rev_cross:
                cur_map[spk] = prev_to_global.get(rev_cross[spk])
            if cur_map.get(spk) is None:
                # 新说话人：分配新全局编号
                cur_map[spk] = f"S{next_available:02d}"
                next_available += 1
        global_maps.append(cur_map)
        prev_raw_map = (cur_map, local_spks)

    # 2. 合并：段内时间 + 段偏移，说话人换全局标签
    merged = []
    for (seg_start, seg_end, seg_idx), segs in zip(segment_plan, all_segment_results):
        m = global_maps[seg_idx]
        for s in segs:
            text = s["text"].strip()
            if not text:
                continue
            merged.append({
                "start": s["start"] + seg_start,
                "end": s["end"] + seg_start,
                "speaker": m.get(s["speaker"], s["speaker"]),
                "text": text,
            })

    # 3. 重叠区去重：相邻段可能把同一句话识别两次（时间戳可能有偏移），
    #    条件：说话人相同 + 开始时间相差不超过重叠区 + 文本高度相似
    merged.sort(key=lambda x: x["start"])
    deduped = []
    for s in merged:
        drop = False
        for prev in reversed(deduped[-8:]):  # 只看最近 8 条
            if s["speaker"] != prev["speaker"]:
                continue
            if abs(s["start"] - prev["start"]) > overlap_sec:
                continue
            sim = SequenceMatcher(
                None, _norm_text(s["text"]), _norm_text(prev["text"])).ratio()
            if sim >= 0.8:
                drop = True  # 保留先出现的（前段完整上下文识别的，更可靠）
                break
        if not drop:
            deduped.append(s)
    return deduped


class SegmentCheckpoint:
    """断点续传：每段完成写 json，重启时跳过。"""

    def __init__(self, work_dir: Path):
        self.path = Path(work_dir) / "progress.json"
        self.done = {}
        if self.path.exists():
            try:
                self.done = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                self.done = {}

    def is_done(self, idx):
        return str(idx) in self.done

    def mark_done(self, idx, segments):
        self.done[str(idx)] = segments
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.done, ensure_ascii=False))
        tmp.replace(self.path)

    def get(self, idx):
        return self.done.get(str(idx))
