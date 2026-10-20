# 声文 ShengWen

> 声音成文 —— 中文录音本地转写工具

**100% 本地运行**的中文音视频转写 Web 应用，适用于会议、讲座、采访、播客等录音场景，内置可扩展的领域热词体系。

## 特点

- 🎯 **高准确率**：MOSS-Transcribe-Diarize（端到端转写+说话人分离），中文实测 CER 23%，专有名词原生识别（无需额外配置）
- 👥 **自动说话人分离**：不需要指定人数，模型自动聚类
- 📦 **0 联网**：转写全本地，模型权重 1.7GB 已内置；只有「AI 深度纠错」可选地用 DeepSeek
- 📄 **多格式输出**：Word、txt、srt、json
- 🔌 **双击启动**：Mac Finder 双击 `start.command` 即可
- 🔄 **断点续传**：长音频分段处理，中断后重新提交自动跳过已完成部分

## 第一次安装

```bash
./install.sh
```

会做：
1. 安装 uv（如果还没有）
2. 创建 Python 3.12 虚拟环境
3. 安装依赖
4. 下载 MOSS 模型（约 1.7GB，需访问 huggingface.co）

## 启动

```bash
# 方式 1：双击
open start.command

# 方式 2：终端启动
./start.command
```

自动打开浏览器 `http://127.0.0.1:8765`。

## AI 深度纠错（可选）

设置页里填 DeepSeek API key（其他 OpenAI 兼容服务也行，如 GLM/通义）：

- API 地址：`https://api.deepseek.com`
- 模型名：`deepseek-chat`

不填也能正常转写，只是少了最后一步的 LLM 文字校对。

## 卸载

```bash
rm -rf transcribe-app
```

彻底干净，模型、venv、数据全部带走。

## 目录结构

```
transcribe-app/
├── start.command         # 双击启动
├── install.sh            # 首次安装
├── README.md             # 本文件
├── .venv/                # Python 3.12 虚拟环境
├── models/hf_cache/      # MOSS 模型权重（1.7GB）
├── vendor/               # MOSS-Transcribe-Diarize 源码
├── server/               # 后端
│   ├── app.py            # Web 服务入口
│   ├── jobs.py           # 任务队列
│   ├── pipeline.py       # 转写流水线
│   ├── moss_engine.py    # MOSS 推理封装
│   ├── segmenter.py      # 分段 + 说话人对齐
│   └── polish.py         # 可选 LLM 纠错
├── web/index.html        # 前端单页
└── runs/                 # 任务数据
    ├── <job_id>/         # 每个任务的目录（上传文件、结果、状态）
    └── config.json       # LLM 设置（api key 等）
```

## 性能参考（M1 Max，MPS GPU 加速）

| 音频时长 | 处理时间 |
|---|---|
| 5 分钟 | ~1 分钟 |
| 30 分钟 | ~6 分钟 |
| 116 分钟 | **~25 分钟**（MPS 加速后，此前 CPU 模式需 4 小时）|

长音频自动后台异步处理，进度实时刷新在 Web 界面（段内也有百分比进度）。

## 技术细节

### 流水线

```
上传(任意格式) → ffmpeg 16k wav
  → 时长 ≤ 12min：整段 MOSS 推理
  → 时长 > 12min：每段 10min + 30s 重叠
      → 逐段推理（模型只加载一次）
      → 重叠区说话人对齐
  → 输出：txt + srt + docx + json
  → [可选] LLM 纠错
```

### 跨段说话人对齐

MOSS 在每段内部独立输出 S01/S02 标签，段间编号不对应。本产品用「重叠区文本相似度」建立映射，链式传递成全局 S01..SN。

### 断点续传

每段完成即写 `runs/<job_id>/progress.json`，重启后自动跳过。重新提交同一录音的同一断点会被复用。

## License

本应用代码以 [MIT](LICENSE) 协议开源（Copyright © 2026 dyunfeng）。

内含的第三方组件遵循其自身许可：
- [MOSS-Transcribe-Diarize](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize)（转写引擎）：Apache-2.0
- [Whisper-Medium](https://github.com/openai/whisper)（编码器）：MIT
- [Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B)（解码器）：Apache-2.0