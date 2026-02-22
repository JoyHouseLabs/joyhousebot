"""YouTube ingest: captions first, then audio transcription fallback."""

import re
import tempfile
from pathlib import Path
from typing import Any

from joyhousebot.agent.tools.ingest.chunking import chunk_text
from joyhousebot.agent.tools.ingest.models import Chunk, IngestDoc


def _parse_srt_or_vtt(content: str) -> str:
    """Extract plain text from SRT or VTT subtitle content."""
    lines = content.strip().split("\n")
    text_parts: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or re.match(r"^\d+$", line) or "-->" in line or line.upper().startswith("WEBVTT"):
            continue
        text_parts.append(line)
    return "\n".join(text_parts)


async def fetch_youtube(
    url: str,
    transcribe_provider: Any = None,
    youtube_processing: str = "auto",
) -> IngestDoc:
    """
    Level A: try to get subtitles via yt-dlp (local).
    Level B: if no subtitles and youtube_processing != local_only, use transcribe_provider (cloud).
    youtube_processing: local_only | allow_cloud | auto (allow_cloud = use cloud transcribe when no subs).
    """
    if youtube_processing == "local_only":
        transcribe_provider = None
    try:
        import yt_dlp
    except ImportError:
        return IngestDoc(
            source_type="youtube",
            source_url=url,
            title="",
            chunks=[Chunk(text="[yt-dlp not installed. Install with: pip install yt-dlp]", start_offset=0, end_offset=0, page=None, meta={})],
            trace={"error": "yt_dlp not installed"},
        )

    title = ""
    # Level A: subtitles
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sub_opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en", "zh", "zh-Hans", "zh-Hant"],
            "outtmpl": str(tmp / "sub"),
        }
        try:
            with yt_dlp.YoutubeDL(sub_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            if info is None:
                return IngestDoc(
                    source_type="youtube",
                    source_url=url,
                    title="",
                    chunks=[],
                    trace={"error": "Failed to extract video info"},
                )
            title = info.get("title") or url
            subtitle_path = None
            for p in tmp.iterdir():
                if p.suffix in (".srt", ".vtt"):
                    subtitle_path = p
                    break
            if subtitle_path and subtitle_path.exists():
                text = _parse_srt_or_vtt(subtitle_path.read_text(encoding="utf-8", errors="ignore"))
                if text.strip():
                    chunks = chunk_text(text, chunk_size=1200, overlap=200, page=None)
                    return IngestDoc(
                        source_type="youtube",
                        source_url=url,
                        title=title,
                        chunks=chunks,
                        trace={"method": "subtitles", "file": subtitle_path.name},
                    )
        except Exception as e:
            pass  # fall through to Level B

        # Level B: audio download + transcribe
        if not transcribe_provider or not hasattr(transcribe_provider, "transcribe"):
            return IngestDoc(
                source_type="youtube",
                source_url=url,
                title=title,
                chunks=[Chunk(text="[No subtitles; configure Groq transcription for audio fallback.]", start_offset=0, end_offset=0, page=None, meta={})],
                trace={"error": "no_subtitles_no_transcribe"},
            )
        try:
            with yt_dlp.YoutubeDL({"format": "bestaudio/best", "outtmpl": str(tmp / "audio.%(ext)s")}) as ydl:
                ydl.download([url])
            # Find the downloaded audio file
            wavs = list(tmp.glob("audio.*"))
            if not wavs:
                return IngestDoc(source_type="youtube", source_url=url, title=title, chunks=[], trace={"error": "audio_download_failed"})
            text = await transcribe_provider.transcribe(wavs[0])
            if not (text and text.strip()):
                return IngestDoc(source_type="youtube", source_url=url, title=title, chunks=[], trace={"error": "transcription_empty"})
            chunks = chunk_text(text, chunk_size=1200, overlap=200, page=None)
            return IngestDoc(
                source_type="youtube",
                source_url=url,
                title=title,
                chunks=chunks,
                trace={"method": "audio_transcribe"},
            )
        except Exception as e:
            return IngestDoc(
                source_type="youtube",
                source_url=url,
                title=title,
                chunks=[Chunk(text=f"[Audio fallback failed: {e}]", start_offset=0, end_offset=0, page=None, meta={})],
                trace={"error": str(e), "method": "audio_fallback"},
            )
