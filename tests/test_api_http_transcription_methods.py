from pathlib import Path

import pytest

from joyhousebot.api.http.transcription_methods import transcribe_upload_file


class _UploadFile:
    filename = "voice.wav"

    async def read(self):
        return b"audio-bytes"


class _Provider:
    def __init__(self, *, fail: bool = False):
        self.fail = fail

    async def transcribe(self, path: Path):
        assert path.exists()
        if self.fail:
            raise RuntimeError("boom")
        return "hello"


@pytest.mark.asyncio
async def test_transcribe_upload_file_success_cleans_temp(tmp_path: Path):
    text = await transcribe_upload_file(
        file=_UploadFile(),
        transcription_provider=_Provider(),
        timestamp=1,
        temp_dir=str(tmp_path),
    )
    assert text == "hello"
    assert not (tmp_path / "joyhousebot_audio_1_voice.wav").exists()


@pytest.mark.asyncio
async def test_transcribe_upload_file_failure_still_cleans_temp(tmp_path: Path):
    with pytest.raises(RuntimeError):
        await transcribe_upload_file(
            file=_UploadFile(),
            transcription_provider=_Provider(fail=True),
            timestamp=2,
            temp_dir=str(tmp_path),
        )
    assert not (tmp_path / "joyhousebot_audio_2_voice.wav").exists()

