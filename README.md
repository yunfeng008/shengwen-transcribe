# 声文 ShengWen

> 声音成文 —— 中文录音本地转写工具。把录音变成带说话人的 Word 文档，全程不联网。

**English**: ShengWen ("sound becomes text") — a 100% local Chinese speech-to-text web app for Mac. Recording in, speaker-labelled Word document out. Built on [MOSS-Transcribe-Diarize](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize) with Apple MPS acceleration.

<!-- TODO: 替换为真实截图
![界面截图](docs/screenshot.png)
-->

| 录音转文字 | 说话人合并与命名 |
|---|---|
| ![上传](docs/screenshot-upload.png) | ![结果](docs/screenshot-result.png) |

*（截图占位：首次发布前补上真实界面图）*

## 为什么做这个

市面上的中文转写方案：云端服务（讯飞等）按小时收费且要上传录音；开源工具（Whisper 系）中文准确率不理想。**声文**用复旦开源的 MOSS-Transcribe-Diarize 模型，在 Mac 上本地跑出接近讯飞的中文准确率——录音永远不离开你的电脑。

## 核心特性

- 🎯 **中文高准确率**：实测字符错误率约 10.5%（以讯飞付费版人工校对稿为基准），专有名词原生识别，无需配置
- 👥 **说话人分离**：模型端到端自动区分讲话人，界面支持手动合并与真名标注
- 🔒 **100% 本地**：转写全程离线，隐私录音（会议/访谈/个人内容）不出本机
- ⚡ **MPS GPU 加速**：Apple Silicon 上比 CPU 模式快约 4 倍，116 分钟录音约 25 分钟完成
- 📄 **多格式导出**：Word（按说话人分段排版）/ txt / srt / json
- 🔄 **断点续传**：长音频分段处理，中断后重跑自动跳过已完成部分
- 📚 **热词体系 + 校对学习**：按领域维护修正词表；人工校对稿可自动提取「错误词→正确词」规则，越用越准
- 🧩 **可选 AI 纠错**：接入 DeepSeek 等任意 OpenAI 兼容 API，进一步修正同音错字（可选，不影响主流程）

## 实测效果

以讯飞付费版（人工校对级）转写结果为基准，116 分钟真实会议录音：

| 指标 | 声文（MOSS） | 备注 |
|---|---|---|
| 字符错误率 CER | **≈10.5%** | 讯飞稿为 0%（基准）|
| 关键术语命中率 | **100%+** | 部分术语超过基准稿 |

## 环境要求

- **Apple Silicon Mac**（M1/M2/M3/M4），建议 ≥16GB 内存
- macOS 12+
- [FFmpeg](https://ffmpeg.org)（`brew install ffmpeg`）
- 首次安装需能访问 GitHub 与 HuggingFace（或其镜像）

## 快速开始

```bash
git clone https://github.com/yunfeng008/shengwen-transcribe.git
cd shengwen-transcribe
./install.sh       # 一键安装（约 10 分钟，含 1.7GB 模型下载，官方源失败自动切国内镜像）
./start.command    # 双击启动，自动打开浏览器
```

然后拖入录音文件即可。也可以从 Releases 单独下载模型权重包，解压到 `models/` 目录。

## AI 深度纠错（可选）

在设置页填入任意 OpenAI 兼容服务的 API Key（DeepSeek / 智谱 / 通义 / Kimi 等），转写完成后可一键调用大模型修正同音错字与术语。不配置也完全可用。

## 工作原理

```
录音 → FFmpeg 16kHz wav → 分段（10 分钟/段 + 30 秒重叠）
  → MOSS 逐段推理（转写 + 段内说话人分离）
  → 跨段说话人对齐 → 热词替换 → 文本顺滑
  → [可选] LLM 深度纠错 → Word / txt / srt / json
```

每段完成即写盘（断点续传）；模型单例常驻（后续任务免加载）。

## 性能参考（M1 Max）

| 录音时长 | 处理时间 |
|---|---|
| 30 分钟 | ~6 分钟 |
| 2 小时 | ~25 分钟 |

## 常见问题

**支持 Windows / Intel Mac 吗？**
不支持。MPS 加速和运行环境均绑定 Apple Silicon；理论上可改造为纯 CPU 跨平台，但未测试。

**说话人编号比实际人数多？**
长录音跨段说话人编号会过度切分，这是当前端到端方案的限制。界面提供手动合并与真名标注，通常几分钟即可整理完毕。

**识别错了怎么改？**
两条路：① 设置页「热词表管理」添加「错误词=>正确词」规则；② 在 Word 里校对完后，通过任务的「校对学习」功能传回，系统自动提取修正规则。

**和讯飞比差距多大？**
文字准确率已非常接近（关键术语 100%+ 追平）；说话人分离的自动化程度有差距，需要少量手动合并。换来的是零费用与完全隐私。

更多技术细节见 [PRODUCT.md](PRODUCT.md)。

## License

本应用代码以 [MIT](LICENSE) 协议开源（Copyright © 2026 dyunfeng）。

内含的第三方组件遵循其自身许可：
- [MOSS-Transcribe-Diarize](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize)（转写引擎）：Apache-2.0
- [Whisper-Medium](https://github.com/openai/whisper)（编码器）：MIT
- [Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B)（解码器）：Apache-2.0

## 致谢

- [OpenMOSS](https://github.com/OpenMOSS) — MOSS-Transcribe-Diarize 模型
- [ModelScope / 魔搭社区](https://modelscope.cn) — 模型分发
- [Apple ML](https://developer.apple.com/machine-learning/) — MPS 推理加速
