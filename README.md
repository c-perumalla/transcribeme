# TranscribeMe

Convert voice notes and meetings into speaker-labelled transcripts, then turn
them into structured notes using a **Claude Skill** as the instruction set.

Runs entirely on your machine except the summarization call to the Claude API.

- **Live capture** from any macOS audio input, with provisional text appearing
  while you record.
- **Dual-channel separation** — with an Aggregate Device, your mic and the
  system audio land on separate channels, so "you" vs "the far end" is exact
  rather than guessed, and only the remote participants need diarizing.
- **Skill-driven summaries** — drop a skill folder in `skills/` and pick it from
  a dropdown.
- **Downloads** as Markdown, plain text, SRT, or JSON.
- **Google Drive delivery** so transcripts show up in a claude.ai Project.

## Setup

```bash
git clone <this repo> && cd transcribeme
cp .env.example .env      # then fill in the tokens below
./run.sh                  # creates the venv, installs deps, starts the server
```

Open <http://127.0.0.1:8000>. The first run downloads ~2GB of model weights.

### 1. Tokens

Put these in `.env`:

- `HUGGINGFACE_TOKEN` — **required for diarization.** First accept the model
  conditions at
  <https://huggingface.co/pyannote/speaker-diarization-community-1> (it is a
  gated model), then create a read token.
- `ANTHROPIC_API_KEY` — required for summaries. Optional if you have run
  `ant auth login`; the SDK picks up that profile automatically.

### 2. Microphone permission

The first recording triggers a macOS permission prompt against **the app that
launched the server** — your terminal or IDE, not TranscribeMe. Approve it under
System Settings → Privacy & Security → Microphone.

If the device opens but no audio arrives, the app fails immediately with a
message saying so rather than letting you record into silence.

### 3. Capturing system audio (optional, but the good part)

macOS has no built-in loopback device, so capturing the far end of a call needs
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
~/Google Drive/My Drive/TranscribeMe/YYYY-MM-DD-<title>.md
```

In claude.ai, create a Project and connect that folder as project knowledge.
Every session after that appears automatically. Change the location with
`DRIVE_DIR`. If the folder's parent does not exist, delivery is skipped with a
warning — it never fails a transcript that otherwise succeeded.

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
| `ASR_BACKEND` | `mlx` | `mlx` uses Metal on Apple Silicon. `faster_whisper` is portable but CPU-only — noticeably slower on a Mac. |
| `MY_NAME` | `Me` | Labels the mic channel in dual-channel recordings. |
| `DIARIZE_MIC_CHANNEL` | `false` | Turn on for in-person meetings sharing one mic. |
| `SEGMENT_SECONDS` | `15` | Live-transcription chunk size. Lower is more responsive, less accurate at the seams. |
| `DRIVE_DIR` | `~/Google Drive/My Drive/TranscribeMe` | Where finished sessions are delivered. |
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
- No authentication — it binds to `127.0.0.1` and is meant for local use only.
