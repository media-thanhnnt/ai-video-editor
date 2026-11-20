# Phase 1.10.1 — Embedded Video Audio Synchronization

## Purpose

A `video_clip` may carry audio. The audio engine now automatically includes it in the final MP4; it is aligned to the **same scene frame origin** as the visual track, with the same overlapping dissolve boundaries.

No manually extracted WAV and no `audio_tracks.start_seconds` calculation are needed.

## Storyboard options

```json
{
  "scene_id": "003",
  "order": 3,
  "type": "video_clip",
  "video": "assets/videos/scene_003.mp4",
  "narration": "",
  "source_audio": {
    "enabled": true,
    "volume": 1.0
  }
}
```

- Omit `source_audio`: automatically play the first audio stream **if present**. Silent videos remain silent.
- `enabled: true`: require an audio stream; asset validation fails if missing.
- `enabled: false`: mute the video's embedded audio.
- `volume`: optional gain, range `0..2`, default `1.0`.
- Source audio runs in parallel with scene voice (if any) and storyboard `audio_tracks`.

## Timeline and dissolve semantics

For `scene 003`, `start_sample = round(start_frame * sample_rate / fps)`; project timeline, not a manual offset, is authoritative. Audio is decoded and resampled to the output sample rate/channel layout and clipped/padded to the scene's exact visual frame duration. If the preceding scene transitions into this one with a 12-frame dissolve at 30 FPS, the incoming audio fades in over 0.4 seconds. If a scene dissolves to the next scene, its source audio fades out over that overlap. Audio layers are combined with `amix`, retaining the pre-existing voice and music layers.

This is a **linear crossfade of embedded-audio sources** rather than video `xfade` automatically providing audio; the result can be louder if several independent music/audio tracks play at once. No audio ducking or loudness normalization yet. The MP4's first audio stream (`0:a:0`) is used. Source-video stream offsets, trim-in/trim-out, playback-speed adjustments, and audio time-stretch are not yet supported. Imported video visuals already normalize to the project FPS.

For an incoming video following a **silent scene**, a fade-in still applies during the overlap so audio arrives gradually; for an outgoing video preceding a **silent scene**, it fades out. A hard cut does not add a fade.

## Migration from Phase 1.10

**Remove any manually extracted duplicate video audio** from `audio_tracks`. Otherwise the same voice/sound will be mixed twice.

Example: delete the `assets/audio/scene_003.wav` track with `start_seconds: 5.8` and keep the original `assets/videos/scene_003.mp4` reference. Then execute:

```powershell
ai-video-editor compose mixed_media_demo --deep
```

The report `reports/audio_report.json` contains `source_video_audio` with the authoritative per-scene `start_sample`, `duration_samples`, `fade_in_samples`, and `fade_out_samples`.

## Tests and QA

The test suite includes multi-video audio-frequency checks before, during, and after dissolve, a disabled embedded-audio case, and silent video handling. `qa --deep` verifies media properties and detects silence but does not assess perceived audio quality, loudness balance, or phase cancellation. Review the finished video by listening to transition boundaries.
