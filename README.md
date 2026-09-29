# TranscribeMe

Convert voice notes and meetings into speaker-labelled transcripts, then turn
them into structured notes using a **Claude Skill** as the instruction set.

Runs entirely on your machine except the summarization call to the Claude API.
Works on **macOS** and **Windows**.

- **Live capture** from any microphone, with provisional text appearing while
  you record.
- **Audio and video files** — upload one, or point it at a **whole folder** and
  it transcribes everything in it with per-file progress.
- **Dual-channel separation** — with an Aggregate Device, your mic and the
  system audio land on separate channels, so "you" vs "the far end" is exact
  rather than guessed, and only the remote participants need diarizing.
- **Skill-driven summaries** — drop a skill folder in `skills/` and pick it from
  a dropdown.
- **Downloads** as Markdown, plain text, SRT, or JSON.
- **Google Drive delivery** so transcripts show up in a claude.ai Project.

## Setup

### Windows

1. Download or clone this repo.
2. Double-click **`setup.cmd`**. It:
   - installs [uv](https://docs.astral.sh/uv/), which brings its own Python
     3.13, so you don't need Python installed
   - installs the Microsoft Visual C++ runtime if it's missing (the only step
     that may show an admin prompt)
   - downloads a pinned, checksum-verified ffmpeg into `.tools\ffmpeg`, without
     changing your PATH
   - installs the exact package versions from `uv.lock`
   - asks for your two tokens (see below)
   - downloads the model weights
   - runs a health check

   It is safe to re-run at any time.
3. Double-click **`run.cmd`**. It opens <http://127.0.0.1:8000>.

On Windows everything runs on the CPU (faster-whisper for speech, pyannote for
speakers). Expect very roughly 1–3× realtime on a modern 8-core laptop.
Diarization is the slow part.

### macOS

```bash
brew install uv ffmpeg
git clone <this repo> && cd transcribeme
cp .env.example .env      # then fill in the tokens below
./run.sh                  # uv installs Python 3.13 + locked deps, starts the server
```

Open <http://127.0.0.1:8000>. The first run downloads ~2GB of model weights.

### Why this setup is reproducible

| Piece | How it is pinned |
|---|---|
| Python | `.python-version` (3.13); uv downloads it, so any system Python is ignored |
| Packages | `uv.lock`, one lockfile resolved for both macOS and Windows; `uv sync --frozen` installs exactly those versions |
| ffmpeg | Windows: a versioned build with a SHA256 check in `scripts/setup.ps1`. macOS: Homebrew |
| Check | `uv run transcribeme doctor` checks ffmpeg, imports, tokens, model access and audio devices |

To upgrade dependencies deliberately, run `uv lock --upgrade`, test, and commit
the new `uv.lock`.

### 1. Tokens

Put these in `.env`:

- `HUGGINGFACE_TOKEN` — **required for diarization.** First accept the model
  conditions at
  <https://huggingface.co/pyannote/speaker-diarization-community-1> (it is a
  gated model), then create a read token.
- `ANTHROPIC_API_KEY` — required for summaries. Optional if you have run
  `ant auth login`; the SDK picks up that profile automatically.

### 2. Microphone permission

**macOS:** the first recording triggers a permission prompt against **the app
that launched the server**. That is your terminal or IDE, not TranscribeMe.
Approve it under System Settings → Privacy & Security → Microphone.

**Windows:** under Settings → Privacy & security → Microphone, turn on both
*Microphone access* and *Let desktop apps access your microphone*.

To test capture outside the app, run `uv run transcribeme doctor --mic`.

If the device opens but no audio arrives, the app fails immediately with a
message saying so rather than letting you record into silence.

### 3. Capturing system audio (optional, but the good part)

**Windows:** recording captures one input device. To include the far end of
a call, either:
- enable **Stereo Mix** (Sound settings → Recording devices; not every sound
  card has it), or
- install **[VB-Audio Virtual Cable](https://vb-audio.com/Cable/)**, set it as
  the call app's speaker, and turn on *Listen to this device* so you still hear
  the call.

Everyone is then diarized from one mixed track. The exact "me vs them" channel
split below is macOS-only for now.

**macOS** has no built-in loopback device, so capturing the far end of a call needs
a virtual driver:

```bash
brew install blackhole-2ch
```

Then in **Audio MIDI Setup**:

1. **Create an Aggregate Device.** Add your microphone **first**, then
   *BlackHole 2ch*. **Order matters** — channel 0 must be the mic, because that
   is what the app splits on. Getting this backwards silently swaps who is who.
2. **Create a Multi-Output Device** containing *BlackHole 2ch* and your normal
   speakers or headphones. Select it as your system output during calls —
   otherwise the app captures the far end but you hear nothing.

Select the Aggregate Device in the app's device dropdown. It is auto-selected
when detected.

To verify the channel order is right, record a few seconds while talking over a
YouTube clip, then check that each track holds the source you expect:

```bash
ffprobe data/sessions/<id>/tracks/me.wav     # should be your voice
ffprobe data/sessions/<id>/tracks/them.wav   # should be the far end
```

For an **in-person** meeting where several people share one mic, set
`DIARIZE_MIC_CHANNEL=true` so the mic channel gets diarized too.

### 4. Connect it to a Claude Project

There is no public API for claude.ai Projects, so the app writes each finished
session as one Markdown file (summary + full transcript) into a Drive folder:

```
~/Google Drive/My Drive/TranscribeMe/YYYY-MM-DD-<title>.md     (macOS)
G:\My Drive\TranscribeMe\YYYY-MM-DD-<title>.md                  (Windows)
```

In claude.ai, create a Project and connect that folder as project knowledge.
Every session after that appears automatically. Change the location with
`DRIVE_DIR`. If the folder's parent does not exist, delivery is skipped with a
warning — it never fails a transcript that otherwise succeeded.

## Transcribing a folder

Point it at a folder and every audio and video file in it is transcribed, one
at a time. Supported types include mp3, m4a, wav, flac, ogg, mp4, mov, mkv,
avi, webm and wmv. Video files just have their first audio track extracted.

**From the web app:** use the **Folder** tab. Paste a path, tick *Include
subfolders* if you want them, and start. A table shows each file's status,
current stage and progress, along with overall progress, audio processed and
an ETA. Every file also becomes a normal session in the list.

**From the command line:**

```bash
uv run transcribeme batch "D:\Recordings" --recursive      # Windows (or drag a folder onto batch.cmd)
uv run transcribeme batch ~/Movies/calls --skill meeting-notes
```

| Option | Meaning |
|---|---|
| `-r, --recursive` | Include subfolders. The output mirrors the folder structure |
| `--skill ID` | Also summarize each file with a skill. This costs one Claude call per file |
| `--speakers N` | Known speaker count for every file |
| `--formats md,srt,txt,json` | Which files to write. The default is `md,srt`; `md` is summary + transcript |
| `--force` | Re-process files that already have outputs |
| `--no-drive` | Don't also copy results to the Drive folder |

Results go to a `transcripts/` folder inside the one you pointed at
(`meeting.mp4` → `transcripts/meeting.md`, `transcripts/meeting.srt`).

- **Resuming:** a file whose outputs already exist is skipped, so if a batch is
  interrupted (Ctrl+C, a reboot), run the same command again to resume.
- **Failures:** a file that fails, such as a video with no audio track, is
  reported and the batch carries on. The CLI exits non-zero if anything failed.
- **One job at a time:** jobs from the web app run one at a time, because two
  Whisper or pyannote jobs at once only thrash the CPU. An upload made during a
  batch shows "queued #N" and slots in between files.

## Writing a skill

A skill is a folder under `skills/` containing `SKILL.md` with YAML frontmatter:

```markdown
---
name: Meeting Notes
description: Shown in the dropdown.
---

# Instructions for Claude...
```

Any other `.md` files in the folder are treated as reference material and
appended automatically. New folders appear in the dropdown without a restart.
See `skills/meeting-notes/SKILL.md` for a worked example.

Use **Re-summarize** to run a different skill against an existing transcript —
it never re-transcribes, and the skill text is prompt-cached, so it is fast and
cheap.

## Configuration

All settings live in `.env`; see `.env.example`. The ones worth knowing:

| Variable | Default | Notes |
|---|---|---|
| `ASR_BACKEND` | `auto` | `mlx` (Metal) on Apple Silicon, `faster_whisper` (CPU) everywhere else. |
| `FASTER_WHISPER_MODEL` | `large-v3-turbo` | On a slow CPU, `small` or `medium` trade accuracy for speed. |
| `CPU_THREADS` | all cores | Threads for faster-whisper. |
| `MY_NAME` | `Me` | Labels the mic channel in dual-channel recordings. |
| `DIARIZE_MIC_CHANNEL` | `false` | Turn on for in-person meetings sharing one mic. |
| `SEGMENT_SECONDS` | `15` | Live-transcription chunk size. Lower is more responsive, less accurate at the seams. |
| `DRIVE_DIR` | `~/Google Drive/My Drive/TranscribeMe` (macOS), `G:/My Drive/TranscribeMe` (Windows) | Where finished sessions are delivered. |
| `CLAUDE_MODEL` | `claude-opus-5` | |

## How it works

```
capture ──> full.wav ──────────────> split ──> me.wav ───> transcribe ──┐
  │                                     │                               ├─> align ─> summarize ─> deliver
  └───────> seg/*.wav ─> live text      └────> them.wav ─> transcribe ──┘         (skill)     (Drive)
                         (provisional)                     + diarize
```

One ffmpeg process writes both a master recording and short segments. The
segments feed provisional live text; the master feeds the accurate pass after
you stop. Live text is replaced wholesale by that pass, so treat it as a preview
— chunk boundaries clip words and it carries no diarization.

Whisper produces word-level timestamps; each word is assigned to the diarization
turn it overlaps most, and consecutive same-speaker words are collapsed into
utterances. That is why speaker boundaries land mid-sentence correctly instead
of being snapped to whole segments.

## Notes and limits

- **Diarization speed** is the bottleneck. On CPU, pyannote runs at roughly
  0.5–1× realtime; MPS is used when available (the startup log says which).
- **Dual-channel is strictly better than mixed** when it applies — your own
  speech skips diarization entirely, so it can never be misattributed.
- **Speaker renaming** (`SPEAKER_00` → a real name) rewrites the transcript and
  re-delivers to Drive. Renames are stored against the original labels, so
  renaming twice works correctly.
- **Uploads and batches are always treated as one mixed track** and diarized.
  Only live recordings use the mic / far-end channel split, so a stereo MP3 or
  a 5.1 movie is never mistaken for a two-party call.
- No authentication — it binds to `127.0.0.1` and is meant for local use only.
  The Folder tab reads any path the server can see, so keep it bound to localhost.
