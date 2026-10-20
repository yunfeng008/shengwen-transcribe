# 声文 ShengWen（项目目录：transcribe-app）

> 面向 AI Agent 的产品说明文档。阅读本文档后，你应能理解本产品的能力边界、目录结构、调用方式，并协助用户完成转写任务、排查问题或进行二次开发。

## 一句话定位

**Mac 本地运行的中文音视频转写 Web 应用**：录音进 → 带说话人的 Word 出。100% 本地推理（隐私安全），仅可选的 AI 纠错一步调用云端 LLM。

- 仓库位置：`transcribe-app/`（自包含目录，`rm -rf` 即完全卸载）
- 平台要求：**Apple Silicon Mac（M 系列芯片）**，macOS 12+，已验证 M1 Max 32GB
- 核心引擎：[MOSS-Transcribe-Diarize](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize)（复旦 OpenMOSS 开源，0.9B，Apache-2.0）

## 核心指标（实测）

| 维度 | 数据 | 说明 |
|---|---|---|
| 文字准确率 | **CER ≈ 10.5%** | 基准为讯飞付费版转写结果（人工校对级）；经 DeepSeek 纠错后关键术语命中率 100%+ |
| 处理速度 | **RTF ≈ 0.17**（MPS 加速） | 116 分钟录音约 25 分钟完成；失败自动回退 CPU（RTF 0.65，约 4 小时）|
| 说话人分离 | 段内准确，全局偏碎 | MOSS 端到端分人；长音频跨段会过度切分（如 10 人录出 23 个编号），产品提供 Web 手动合并 |
| 成本 | ¥0（默认）/ ~¥0.3（可选 AI 纠错） | 转写全本地免费；纠错用 DeepSeek API |
| 内存常驻 | ~4 GB（CPU fp32）/ ~2 GB（MPS fp16） | 模型加载一次常驻，后续任务免加载 |

## 技术架构

```
上传(任意格式) → FFmpeg 16kHz wav → 分段(10min/段 + 30s重叠)
  → MOSS 逐段推理（MPS fp16，模型单例常驻）
  → 跨段说话人对齐（文本重叠匹配 + 碎片合并）
  → 热词替换（assets/hotwords.txt，错误词=>正确词）
  → 文本顺滑（去口吃、清噪声）
  → [可选] DeepSeek 深度纠错（领域感知提示词）
  → 输出 result.json / .txt / .srt / .docx
```

技术栈：FastAPI + uvicorn（Web）、PyTorch 2.14（MPS/CPU 推理）、python-docx（Word）、FFmpeg（音频）。运行于 uv 管理的独立 Python 3.12 venv（`.venv/`）。

## 快速使用

### Web 界面（主推，给人用）

```bash
open transcribe-app/start.command   # 双击也可，自动开浏览器 http://127.0.0.1:8765
```

页面上传录音 → 进度条（段级 + 段内 token 级百分比）→ 完成后下载 Word / 合并说话人 / 可选 AI 纠错。

### 命令行（给 agent 用）

```bash
PY=transcribe-app/.venv/bin/python
$PY -u transcribe-app/server/pipeline.py <音频文件> <输出目录> [--prompt "热词..."]
```

- 结果在 `<输出目录>/result.json|txt|srt|docx`
- 长音频建议 nohup 后台跑，日志可实时 tail；进度以 `[transcribe] NN% 段 X/Y...` 格式输出
- 断点续传：同一输出目录重跑会跳过 checkpoint（`progress.json`）里已完成的段

### HTTP API（给 agent 或前端用）

服务启动后（`server/app.py --port 8765`）：

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/jobs` | POST | multipart 上传（file, polish, prompt, domain），返回任务 |
| `/api/jobs` | GET | 任务列表 |
| `/api/jobs/{id}` | GET / DELETE | 任务状态（含 progress/message）/ 删除 |
| `/api/jobs/{id}/speakers` | GET | 说话人统计（id/name/句数/字数）|
| `/api/jobs/{id}/merge` | POST | 合并/命名说话人 `{targets:[int], keep:int, name?:str}` |
| `/api/jobs/{id}/download/{kind}` | GET | kind ∈ txt/docx/json |
| `/api/settings` | GET / POST | LLM 纠错配置（base 必须强制 https）|

## 关键文件

```
transcribe-app/
├── start.command            # 双击启动（自动开浏览器）
├── install.sh               # 首次安装（检查 ffmpeg → uv → venv → 依赖 → 模型）
├── assets/                  # 各领域热词表：hotwords_<domain>.txt（general/religion...）
│                            #   格式：错误词=>正确词 每行一条，转写后自动替换
├── server/
│   ├── app.py               # FastAPI 入口与全部 REST 端点
│   ├── jobs.py              # 单 worker 串行任务队列；job.json 状态持久化
│   ├── pipeline.py          # 流水线：转码/分段/推理/对齐/热词/导出
│   ├── moss_engine.py       # MOSS 单例引擎（MPS 优先，CPU 回退）
│   ├── segmenter.py         # 切段计划 + checkpoint + 对齐合并
│   ├── polish.py            # DeepSeek 纠错（配置在 runs/config.json）
│   └── fmt_utils.py         # 从 result.json 重写 txt/srt/docx（支持真名 speaker_names）
├── web/index.html           # 单页前端（无构建依赖）
├── models/hf_cache/         # MOSS 权重 1.7GB（离线模式，HF_HUB_OFFLINE=1）
├── vendor/MOSS-Transcribe-Diarize/  # 推理代码（上游 Apache-2.0）
└── runs/<job_id>/           # 任务数据：progress.json（断点）+ result.*
```

## 特色能力

1. **多领域支持**：上传时选择录音领域（通用/信仰/会议/讲座），热词表和 AI 纠错提示词随之切换；引擎本身领域无关
2. **断点续传**：每段完成即写 `runs/<job>/progress.json`，进程被杀后重跑自动跳过已完成段
3. **热词自动修正**：`assets/hotwords_<domain>.txt` 维护各领域术语映射（`错误词=>正确词` 每行一条），导出前自动全文替换；新错误追加对应文件即可
4. **说话人手动合并 + 真名**：`merge` API 或 Web 面板操作后自动重写全部格式文件；`result.json` 的 `speaker_names` 字段存真名映射，Word 直接显示
5. **优雅降级**：MPS 不可用回退 CPU；python-docx 缺失跳过 Word；LLM key 缺失跳过纠错——任何一环失败都不阻塞主流程
6. **成本极低**：默认零成本；AI 纠错按 DeepSeek 计价约 ¥0.3/次（116 分钟录音 39 次调用）

## 已知限制（诚实边界）

- **仅支持 Apple Silicon Mac**：MPS 加速和 venv 均绑定 ARM64 macOS；Intel/Windows 不适用
- **说话人全局编号会碎**：长录音的段间编号不可靠是 MOSS 架构限制（无显式声纹输出），实测声纹方案（CAM++/ECAPA）在此类远场录音上区分力不足已放弃；产品解法是 Web 手动合并
- **多语言噪声**：MOSS 对背景含混音可能输出少量日/韩文碎片，已有顺滑与 LLM 纠错两道处理
- **`speaker_names` 是显示层**：Word/txt/srt 会显示真名，但 `spk_display` 数字 ID 不变
- **JobManager 单进程**：重启服务后运行中的任务标记 failed（分段进度保留）；`job.json` 需在服务启动前存在才会被加载

## 维护操作

```bash
# 重新从 checkpoint 生成全部格式文件（说话人合并/改名后）
PYTHONPATH=transcribe-app/server $PY -c "
from fmt_utils import regenerate_outputs
import json
data = json.load(open('runs/<job>/result.json'))
regenerate_outputs('runs/<job>', data)"

# 磁盘管理：每个完成任务约占 220MB（wav）+ 原始上传；Web 删除按钮即彻底清理
# 查看模型设备：日志首行会打印「使用 MPS (fp16)」或「回退 CPU」
```

## 卸载

```bash
rm -rf transcribe-app        # 一切都在这个目录里
rm -rf ~/.local/share/uv/python/cpython-3.12*   # 可选：删独立 Python
```

---

*本文档由产品实际构建过程生成，所有指标均为实测值（2026-09，M1 Max / macOS 26）。*
