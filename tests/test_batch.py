import subprocess
from pathlib import Path

import pytest

from app import audio, batch, config


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_scan_filters_and_sorts_naturally(tmp_path):
    for name in ["part 10.mp4", "part 2.mkv", "notes.txt", ".hidden.mp3",
                 "sub/deep.m4a", "transcripts/old.wav"]:
        touch(tmp_path / name)

    flat = [p.relative_to(tmp_path).as_posix() for p in batch.scan(tmp_path)]
    assert flat == ["part 2.mkv", "part 10.mp4"]

    deep = [p.relative_to(tmp_path).as_posix() for p in batch.scan(tmp_path, recursive=True)]
    assert deep == ["part 2.mkv", "part 10.mp4", "sub/deep.m4a"]


def test_output_paths_mirror_subfolders(tmp_path):
    out = batch.output_paths(tmp_path, tmp_path / "a" / "call.mp4", ("md", "srt"))
    assert out["md"] == tmp_path / "transcripts" / "a" / "call.md"
    assert out["srt"] == tmp_path / "transcripts" / "a" / "call.srt"


def test_plan_skips_done_and_disambiguates_stems(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "probe", lambda p: {"duration_s": 60.0, "channels": 1,
                                                   "sample_rate": 16000})
    touch(tmp_path / "talk.mp4")
    touch(tmp_path / "talk.m4a")
    touch(tmp_path / "done.mp3")
    for fmt in ("md", "srt"):
        touch(tmp_path / "transcripts" / f"done.{fmt}")

    items = {i["rel_path"]: i for i in batch.plan(batch.Options(folder=tmp_path))}
    assert items["done.mp3"]["status"] == "skipped"
    assert items["talk.mp4"]["outputs"]["md"].endswith("talk.mp4.md")
    assert items["talk.m4a"]["outputs"]["md"].endswith("talk.m4a.md")

    forced = batch.plan(batch.Options(folder=tmp_path, force=True))
    assert all(i["status"] == "queued" for i in forced)


def test_bad_format_rejected(tmp_path):
    with pytest.raises(batch.BatchError):
        batch.Options(folder=tmp_path, formats=("docx",))


def _make_video(dst: Path, audio_src: Path | None) -> Path:
    args = [config.FFMPEG, "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=160x120:d=3"]
    if audio_src:
        args += ["-i", str(audio_src), "-shortest", "-ac", "2", "-c:a", "aac"]
    args += ["-c:v", "mpeg4", str(dst)]
    subprocess.run(args, check=True)
    return dst


def test_extract_audio_from_stereo_video(tmp_path, speech_wav):
    video = _make_video(tmp_path / "clip.mp4", speech_wav)
    info = audio.extract(video, tmp_path / "audio.wav")
    assert info["channels"] == 2
    out = audio.probe(tmp_path / "audio.wav")
    assert (out["channels"], out["sample_rate"]) == (1, 16000)
    assert out["duration_s"] > 2


def test_extract_video_without_audio_is_clear(tmp_path):
    video = _make_video(tmp_path / "silent.mp4", None)
    with pytest.raises(audio.AudioError, match="has no audio track"):
        audio.extract(video, tmp_path / "audio.wav")
