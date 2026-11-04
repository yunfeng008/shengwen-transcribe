"""ffmpeg/ffprobe 二进制定位。

优先用系统安装的 ffmpeg/ffprobe（brew 等）；没有系统版本时，
回退到 imageio-ffmpeg pip 包自带的静态二进制——让小白用户
不装 brew/ffmpeg 也能完成安装（依赖随 venv 一起装好）。
"""

import re
import shutil
import subprocess


def ffmpeg():
    """返回可用的 ffmpeg 命令（系统优先，否则 imageio-ffmpeg 静态版）。"""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _ffmpeg_duration(path):
    """无 ffprobe 时，用 ffmpeg -i 的 stderr 解析时长（imageio-ffmpeg 不带 ffprobe）。"""
    proc = subprocess.run(
        [ffmpeg(), "-i", str(path)],
        capture_output=True, text=True, check=False,
    )
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not m:
        raise RuntimeError(f"无法读取媒体时长（且系统无 ffprobe）: {path}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def get_duration(path):
    """媒体时长（秒）。系统 ffprobe 优先，否则 ffmpeg 兜底。"""
    if shutil.which("ffprobe"):
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ]).decode().strip()
        return float(out)
    return _ffmpeg_duration(path)
