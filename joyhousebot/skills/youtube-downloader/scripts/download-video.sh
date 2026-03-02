#!/bin/bash

set -e

DEFAULT_OUTPUT_DIR="${VIDEO_DOWNLOAD_DIR:-/Users/joyhouse/video}"
YTDLP_BIN="${YTDLP_BIN:-yt-dlp}"

usage() {
    echo "Usage: $0 [OPTIONS] <URL>"
    echo ""
    echo "Download video from YouTube or other supported platforms"
    echo ""
    echo "Options:"
    echo "  -o, --output DIR     Output directory (default: $DEFAULT_OUTPUT_DIR)"
    echo "  -f, --format FORMAT  Video format (default: best)"
    echo "  -s, --subs           Download subtitles if available"
    echo "  -a, --auto-subs      Download auto-generated subtitles"
    echo "  -q, --quality QUAL   Quality: 720p, 1080p, best (default: best)"
    echo "  -h, --help           Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 \"https://www.youtube.com/watch?v=VIDEO_ID\""
    echo "  $0 -q 1080p -s \"https://www.youtube.com/watch?v=VIDEO_ID\""
    echo "  $0 -o ~/Downloads \"https://www.youtube.com/watch?v=VIDEO_ID\""
}

check_deps() {
    if ! command -v "$YTDLP_BIN" &> /dev/null; then
        echo "Error: $YTDLP_BIN not found. Please install yt-dlp."
        echo "Visit: https://github.com/yt-dlp/yt-dlp#installation"
        exit 1
    fi
}

get_format() {
    local quality="$1"
    case "$quality" in
        720p)
            echo "bestvideo[height<=720]+bestaudio/best[height<=720]"
            ;;
        1080p)
            echo "bestvideo[height<=1080]+bestaudio/best[height<=1080]"
            ;;
        best|*)
            echo "bestvideo+bestaudio/best"
            ;;
    esac
}

download_subs=""
download_auto_subs=""
format="best"
output_dir="$DEFAULT_OUTPUT_DIR"
url=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--output)
            output_dir="$2"
            shift 2
            ;;
        -f|--format)
            format="$2"
            shift 2
            ;;
        -s|--subs)
            download_subs="--write-subs"
            shift
            ;;
        -a|--auto-subs)
            download_auto_subs="--write-auto-subs"
            shift
            ;;
        -q|--quality)
            format=$(get_format "$2")
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
        *)
            url="$1"
            shift
            ;;
    esac
done

if [ -z "$url" ]; then
    echo "Error: URL is required"
    usage
    exit 1
fi

check_deps

mkdir -p "$output_dir"

echo "=========================================="
echo "YouTube Video Downloader"
echo "=========================================="
echo "URL: $url"
echo "Output: $output_dir"
echo "Format: $format"
echo "=========================================="
echo ""

cmd="$YTDLP_BIN -f \"$format\" -o \"$output_dir/%(title)s.%(ext)s\" $download_subs $download_auto_subs \"$url\""

echo "Executing: $cmd"
echo ""

eval "$cmd"

echo ""
echo "=========================================="
echo "Download complete!"
echo "=========================================="

echo ""
echo "Downloaded files:"
ls -lh "$output_dir" | tail -5