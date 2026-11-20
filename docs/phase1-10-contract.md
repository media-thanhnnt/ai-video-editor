# Phase 1.10 — Mixed Media Timeline, Audio and Subtitles

## Goal

Accept one storyboard mixing three scene source types:

- `image`: PNG/JPG + narration WAV/MP3; existing zoom/pan render
- `image_sequence`: explicit contiguous numbered PNG/JPG frames
- `video_clip`: existing MP4/MOV/MKV from `assets/videos/`, even if source FPS differs from output FPS

The final MP4 contains H.264 video and AAC-LC audio. Optional global audio tracks can be mixed over the scene voice, and subtitles can be emitted as `.srt`, burned into video, or both.

## Pipeline

`init -> validate -> assets -> plan -> timeline -> render --resume -> audio -> qa --deep`

Alternatively: `ai-video-editor compose PROJECT_ID --deep` orchestrates all steps. Reuse cache by default. `compose --force` invalidates render cache.

## Schema additions in storyboard.json (schema_version remains 1.1)

```json
{
  "type": "video_clip",
  "video": "assets/videos/clip.mp4",
  "narration": "",
  "scene_id": "003",
  "order": 3
}
```

Optional scene voice: add `"audio":"assets/audio/voice.wav"` and nonempty `narration`. The decoded narration plus head/tail padding must fit within the source video duration. Input video AUDIO IS NOT automatically imported; to preserve sounds, extract them into an audio file and add a timed `audio_tracks` entry.

Optional independent audio (e.g. background or sound effects):

```json
"audio_tracks": [
  {"file":"assets/audio/background.wav","start_seconds":0,"volume":0.15}
]
```

All supplementary audio is decoded to output sample rate and mixed with the main timeline voice. `volume` is a linear multiplier from 0 to 2, NOT dB. Start is absolute video time. Audio extending beyond the video is cropped by the final mix duration. This initial implementation does not apply automatic ducking, looping, loudness mastering, or source-video soundtrack retention.

Authored captions:

```json
"subtitles": [
  {"start_seconds":0,"end_seconds":1.2,"text":"Một câu chuyện bắt đầu."}
]
```

Or put an existing UTF-8 SRT under `assets/subtitles/` and set:

```json
"subtitle_file": "assets/subtitles/captions.srt"
```

Specify `subtitles` **or** `subtitle_file`, not both. Timestamps are absolute project timeline time. There is no automatic speech recognition or word-level subtitle alignment. Generated SRT is `output/subtitles.srt`.

Set `config.json` `subtitles.mode` to:

- `off`: SRT is still exported if cues are present, but nothing is embedded in MP4
- `sidecar`: generate external SRT; copy video stream without re-encoding
- `burn_in`: burn captions to video (FFmpeg with libass needed; re-encode to H.264)
- `both`: generate SRT and burn into video

## Existing video input behavior

- Clip uses its measured media duration, quantized to project output FPS.
- Normalize source frames to project size and FPS; source 24 FPS can be converted to output 30 FPS (frames are duplicated or dropped; no AI interpolation).
- Scene effects motion must be `static` for `video_clip` and `image_sequence`.
- Transition effects remain available; overlapping transitions shorten the final running time.
- Cached scenes are keyed by file SHA256 and all relevant render settings.
- Original clip audio is discarded intentionally to avoid unplanned mixes; use separate audio tracks to retain it.
- Video clips longer than needed should be trimmed with FFmpeg before importing; trim-in/trim-out per scene is not part of this phase.

## Safe migration from Phase 1.9

Back up project data. Replace code/config/docs/tests with the Phase 1.10 ZIP, **do not overwrite `projects`**. Then reinstall locally:

```powershell
py -m pip install -e .
py -m unittest discover -s tests -v
```

Existing Phase 1.9 `image` and `image_sequence` storyboard JSON files require no schema migration.

## QA acceptance

Expected: all unit tests pass, project `qa --deep` PASS, output counts match timeline, no color metadata mismatch, SRT present when captions configured, audible track mix, and manual visual inspection confirms intended transitions/subtitles.

**Audio duration tolerance:** AAC works in 1024-sample access units. The QA implementation accepts a decoded sample difference within 2048 samples (including priming/padding); an exact zero is not guaranteed when additional audio is mixed.
