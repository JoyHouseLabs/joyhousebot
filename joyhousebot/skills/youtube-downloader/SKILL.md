---
name: youtube-downloader
description: Download videos from YouTube and other supported platforms using yt-dlp
metadata: {"joyhousebot":{"emoji":"🎥","os":["darwin","linux"],"requires":{"bins":["yt-dlp","yt-dlp"]}}}
---

# YouTube Downloader Skill

Download videos from YouTube and other supported platforms using yt-dlp.

## When to use (trigger phrases)

Use this skill immediately when the user asks any of:
- "download this YouTube video"
- "save this video from YouTube"
- "get this video: [URL]"
- "download video from [URL]"
- "download from YouTube"

## Quick start

```bash
# Download video (best available format)
yt-dlp "https://www.youtube.com/watch?v=VIDEO_ID"

# Download video with subtitles
yt-dlp --write-subs --write-auto-subs "https://www.youtube.com/watch?v=VIDEO_ID"

# Download audio only
yt-dlp -x --audio-format mp3 "https://www.youtube.com/watch?v=VIDEO_ID"

# Download specific quality
yt-dlp -f "bestvideo[height<=1080]+bestaudio/best[height<=1080]" "https://www.youtube.com/watch?v=VIDEO_ID"

# Download to specific directory
yt-dlp -o "/Users/joyhouse/video/%(title)s.%(ext)s" "https://www.youtube.com/watch?v=VIDEO_ID"
```

## Supported platforms

yt-dlp supports thousands of websites including:
- YouTube (youtube.com, youtu.be, youtube-nocookie.com)
- Vimeo
- Dailymotion
- Facebook
- Twitter
- Instagram
- And many more

## Common options

- `-f FORMAT` - Specify format (best, bestvideo+bestaudio, etc.)
- `-o OUTPUT` - Output filename template
- `--write-subs` - Download available subtitles
- `--write-auto-subs` - Download auto-generated subtitles
- `-x` - Extract audio only
- `--audio-format FORMAT` - Audio format (mp3, m4a, etc.)
- `--proxy PROXY` - Use proxy
- `--no-playlist` - Download single video only (default)
- `--yes-playlist` - Download entire playlist

## Format selection

```bash
# Best quality (video+audio)
yt-dlp -f "bestvideo+bestaudio/best"

# Best quality up to 1080p
yt-dlp -f "bestvideo[height<=1080]+bestaudio/best[height<=1080]"

# Best quality up to 720p
yt-dlp -f "bestvideo[height<=720]+bestaudio/best[height<=720]"

# Audio only (best quality)
yt-dlp -f "bestaudio"
```

## Output templates

Common placeholders:
- `%(title)s` - Video title
- `%(id)s` - Video ID
- `%(uploader)s` - Uploader name
- `%(upload_date)s` - Upload date
- `%(ext)s` - File extension

```bash
# Custom output path
yt-dlp -o "/Users/joyhouse/video/%(uploader)s/%(title)s.%(ext)s" "URL"
```

## Troubleshooting

### JavaScript runtime warning
If you see: "No supported JavaScript runtime could be found"

Install deno (recommended):
```bash
brew install deno
```

Or add `--js-runtimes deno` to your command.

### ffmpeg not found
Install ffmpeg:
```bash
brew install ffmpeg
```

### Network issues
Use proxy or VPN if needed:
```bash
yt-dlp --proxy "socks5://127.0.0.1:1080" "URL"
```

## Integration with other skills

Combine with:
- **summarize**: Download then summarize content
- **whisper**: Download then transcribe audio for Chinese subtitles
- **transcribe**: Use built-in transcription services

## Examples

```bash
# Download and save to video directory
yt-dlp -o "/Users/joyhouse/video/%(title)s.%(ext)s" "https://www.youtube.com/watch?v=54ha_VGfaGw"

# Download with auto subtitles for later transcription
yt-dlp --write-auto-subs --sub-lang zh-Hans "https://www.youtube.com/watch?v=VIDEO_ID"
```

## Notes

- Default download location: current directory
- Always use quotes around URLs
- Video files can be large; ensure sufficient disk space
- Check copyright and terms of service before downloading