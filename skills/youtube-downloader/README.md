# YouTube Downloader Skill

一个用于从YouTube和其他平台下载视频的skill。

## 功能特性

- 支持YouTube和其他数千个网站
- 可选字幕下载
- 音频提取功能
- 质量选择（720p, 1080p, best）
- 自定义输出路径
- 与whisper转录集成

## 安装要求

- `yt-dlp` (YouTube下载器)
- 可选: `ffmpeg` (用于音频提取和格式转换)
- 可选: `deno` (JavaScript运行时，解决YouTube API问题)

## 快速开始

### 使用脚本下载

```bash
# 基本下载
./scripts/download-video.sh "https://www.youtube.com/watch?v=VIDEO_ID"

# 下载1080p视频并获取字幕
./scripts/download-video.sh -q 1080p -s "https://www.youtube.com/watch?v=VIDEO_ID"

# 下载到指定目录
./scripts/download-video.sh -o ~/Downloads "https://www.youtube.com/watch?v=VIDEO_ID"
```

### 直接使用yt-dlp

```bash
# 下载视频
yt-dlp "https://www.youtube.com/watch?v=VIDEO_ID"

# 下载带字幕的视频
yt-dlp --write-subs --write-auto-subs "https://www.youtube.com/watch?v=VIDEO_ID"

# 仅下载音频
yt-dlp -x --audio-format mp3 "https://www.youtube.com/watch?v=VIDEO_ID"
```

## 与其他skill集成

### + whisper (视频转录)

下载视频后，使用whisper进行中文转录：

```bash
# 下载视频
./scripts/download-video.sh "https://www.youtube.com/watch?v=VIDEO_ID"

# 使用whisper转录
whisper "video.mp4" --task transcribe --language zh --model base
```

### + summarize (内容总结)

下载后使用summarize进行内容总结：

```bash
summarize "video.mp4" --model google/gemini-3-flash-preview
```

## 常见问题

### JavaScript运行时警告

如果看到："No supported JavaScript runtime could be found"

解决方案：
```bash
brew install deno
```

### ffmpeg未找到

安装ffmpeg：
```bash
brew install ffmpeg
```

## 配置环境变量

可以在 `.zshrc` 或 `.bashrc` 中设置：

```bash
# 视频下载目录
export VIDEO_DOWNLOAD_DIR="/Users/joyhouse/video"

# yt-dlp二进制文件路径
export YTDLP_BIN="/path/to/yt-dlp"
```

## 文件结构

```
youtube-downloader/
├── SKILL.md          # Skill配置和文档
├── README.md         # 本文件
└── scripts/
    └── download-video.sh  # 下载脚本
```

## 示例工作流

```bash
# 1. 下载YouTube视频
./scripts/download-video.sh -q 1080p "https://www.youtube.com/watch?v=54ha_VGfaGw"

# 2. 使用whisper进行中文转录
whisper "佛学全景地图 ｜ 2500年宗派经典一期讲透 ｜ 王利杰认知系列 [54ha_VGfaGw].mp4" \
    --task transcribe \
    --language zh \
    --model base \
    --output_format txt,vtt,srt

# 3. 查看转录结果
cat "佛学全景地图 ｜ 2500年宗派经典一期讲透 ｜ 王利杰认知系列 [54ha_VGfaGw].txt"
```

## 注意事项

- 确保有足够的磁盘空间
- 遵守版权和YouTube服务条款
- 下载速度取决于网络连接
- 视频文件可能很大，注意磁盘空间

## 支持的平台

yt-dlp支持数千个网站，包括：
- YouTube
- Vimeo
- Dailymotion
- Facebook
- Twitter
- Instagram
- Bilibili
- 以及更多...