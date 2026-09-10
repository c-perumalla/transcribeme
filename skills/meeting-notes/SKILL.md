---
name: Meeting Notes
description: Turns a diarized meeting transcript into decisions, action items with owners, and open questions.
---

# Meeting Notes

Turn the transcript into notes someone who missed the meeting can act on without
listening to the recording.

## Output structure

Produce exactly these sections, in this order. Omit a section entirely if it
would be empty — never write "None" or "N/A" under a heading.

### TL;DR
Three to five bullets covering what the meeting was about and what came out of
it. Someone reading only this section should know whether they need to read the
rest.

### Decisions
Each decision as a bullet: what was decided, and — where the transcript supports
it — the reasoning. Record only things actually settled. A topic that was
discussed without resolution belongs under Open Questions, not here.

### Action Items
A Markdown table with columns: **Owner**, **Action**, **Due**, **Timestamp**.

- Owner is the speaker who committed to it. If nobody clearly owned it, write
  `Unassigned` — do not guess.
- Due is whatever was stated ("next Tuesday", "before launch"). If nothing was
  stated, leave it blank.
- Timestamp is the `[HH:MM:SS]` where the commitment was made, so the reader can
  jump to it.

### Open Questions
Anything raised and left unresolved, and anything blocked pending an answer.
Name who raised it.

### Notable Quotes
Up to three verbatim quotes that carry real weight — a firm commitment, a
significant objection, a crisp statement of a problem. Attribute each to its
speaker with a timestamp. Skip this section if nothing rises to that bar.

## Conventions

- Write in past tense, third person. No second-person address to the reader.
- Refer to people by the speaker labels used in the transcript. If a label is
  generic (`SPEAKER_01`) but the transcript makes the real name obvious from how
  others address them, use the real name and note the mapping once at the top.
- Keep bullets to one or two sentences. Trim filler, hedging, and small talk.
- The transcript is machine-generated. Silently correct obvious mis-transcriptions
  where context makes the intent clear; where a critical term is garbled beyond
  recovery, mark it `[unclear]` rather than guessing.
- Never invent an owner, a due date, or a decision that is not in the transcript.
