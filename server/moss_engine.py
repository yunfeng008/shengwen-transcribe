#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MOSS 推理引擎封装：模型加载一次，循环推理多段音频。

设备策略：优先 MPS（Apple GPU，实测 4 倍速），失败自动回退 CPU。
模型缓存隔离在产品目录内。
"""

import os
import sys
import time
from pathlib import Path

# 产品根目录
APP_ROOT = Path(__file__).parent.parent.resolve()

# 把 MOSS 代码加入 path
VENDOR = APP_ROOT / "vendor" / "MOSS-Transcribe-Diarize"
sys.path.insert(0, str(VENDOR))

# 模型缓存隔离在产品目录内；模型已下载，强制离线模式避免启动时联网验证超时
os.environ.setdefault("HF_HOME", str(APP_ROOT / "models" / "hf_cache"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

MODEL_ID = "OpenMOSS-Team/MOSS-Transcribe-Diarize"

# MOSS 输出约 12.5 token/秒（含时间戳和说话人标记），留 1.5 倍安全系数
TOKENS_PER_SECOND = 12.5
SAFETY_FACTOR = 1.5


class MossEngine:
    """单例引擎：加载一次模型，反复推理。"""

    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        self.torch = torch
        t0 = time.time()
        print("[engine] 加载 MOSS 模型（首次约 20-60 秒）...", flush=True)

        self.model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            trust_remote_code=True,
            dtype="auto",
            attn_implementation="sdpa",  # eager 显存平方增长，sdpa 最优
        )
        self.processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)

        # 优先 MPS（实测 RTF 0.17 vs CPU 0.65，约 4 倍速，输出与 CPU 一致）
        try:
            self.device = torch.device("mps")
            self.dtype = torch.float16
            self.model = self.model.to(dtype=self.dtype).to(self.device).eval()
            print(f"[engine] 使用 MPS (fp16)，加载耗时 {time.time() - t0:.0f}s", flush=True)
        except Exception as e:
            self.device = torch.device("cpu")
            self.dtype = torch.float32
            self.model = self.model.to(dtype=self.dtype).to(self.device).eval()
            print(f"[engine] MPS 不可用（{e}），回退 CPU，加载耗时 {time.time() - t0:.0f}s", flush=True)

    def transcribe(self, audio_path, prompt=None, progress_callback=None):
        """转写一段音频，返回 (segment 列表, 原始文本)。

        audio_path: 音频文件路径（16k wav 最佳）
        prompt: 可选 prompt 后缀（热词等）
        progress_callback: fn(tokens_generated)，每生成若干 token 回调一次
        """
        from moss_transcribe_diarize import parse_transcript
        from moss_transcribe_diarize.inference_utils import (
            build_transcription_messages,
            generate_transcription,
        )

        messages = build_transcription_messages(audio_path, prompt=prompt or "")
        # 按音频时长动态算 max_new_tokens，避免默认 2048 截断长段
        duration = _audio_duration(audio_path)
        max_new = max(2048, int(duration * TOKENS_PER_SECOND * SAFETY_FACTOR))

        kwargs = {}
        if progress_callback is not None:
            kwargs["token_callback"] = progress_callback
        result = generate_transcription(
            self.model,
            self.processor,
            messages,
            max_new_tokens=max_new,
            device=self.device,
            dtype=self.dtype,
            **kwargs,
        )
        segments = parse_transcript(result["text"])
        return [
            {"start": s.start, "end": s.end, "speaker": s.speaker, "text": s.text}
            for s in segments
        ], result.get("text", "")


def _audio_duration(path):
    """ffprobe 取音频时长（秒）。"""
    import subprocess
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]).decode().strip()
    return float(out)
