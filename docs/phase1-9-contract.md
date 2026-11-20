# Phase 1.9 — Image Sequence Scenes

## Scope

- Adds `type: "image_sequence"` to storyboard scenes, alongside legacy `type: "image"` (or omitted `type`).
- Every source image is one output frame, in numeric order from **1** through `frame_count`.
- Example: 90 consecutive images at 30/1 FPS = **90 frames, exactly 3 seconds** (for a standalone scene with no overlap).
- Does not synthesize, interpolate, morph or generate intermediate images. Motion must already exist in the frames.
- No Zoom/Pan is applied to sequence scenes; planning forces `motion: "static"`.
- Compatible with still-image scenes, crossfade/dissolve/hard-cut transitions, audio mixing, caching/recovery, and QA.
- Silent image-sequence scenes require no `audio` and receive timeline-accurate silent AAC when exported with `audio` CLI.
- If an `audio` path is present, decoded voice duration plus head/tail minimum padding must fit within fixed sequence frames. If not, timeline fails without trimming narration.
- Audio for old still-image scenes continues to be required.
- Frame sequences must use **the same FPS** as project output (no frame-rate conversion).
- Transitions across scenes can overlap, so **overall project duration** may be shorter than the sum of the standalone scene durations.

## Required scene schema

```json
{
  "scene_id": "001",
  "order": 1,
  "type": "image_sequence",
  "narration": "",
  "sequence": {
    "frames_dir": "assets/sequences/throw_ball",
    "frame_pattern": "frame_%04d.png",
    "frame_count": 90,
    "fps": "30/1"
  }
}
```

Place the images in `projects/<id>/assets/sequences/throw_ball/frame_0001.png` ... `frame_0090.png`.

Supported filename patterns: `frame_%04d` or `frame_%05d`, extension `.png`, `.jpg`, `.jpeg`.
All frames must exist, be decodable, have a consistent size, remain inside the project folder and match their extensions.
Max `frame_count`: 18,000. Frames are checked individually; large sequences increase asset-scan time and disk usage.

`image.fit_mode` supports `cover` and `contain` on sequence scenes. `blur_fill` is not yet supported for image sequences. Project output width/height must be even.

## Workflow on Windows

```powershell
ai-video-editor init ball_throw_demo --title "Ném bóng"
# Copy example storyboard to projects/ball_throw_demo/storyboard.json
# Copy 90 images to projects/ball_throw_demo/assets/sequences/throw_ball/
ai-video-editor validate ball_throw_demo
ai-video-editor assets ball_throw_demo
ai-video-editor plan ball_throw_demo --style documentary --seed 12345
ai-video-editor timeline ball_throw_demo --use-plan
ai-video-editor render ball_throw_demo --resume
ai-video-editor audio ball_throw_demo
ai-video-editor qa ball_throw_demo --deep
```

`video_only.mp4` has no audio, `final.mp4` has one AAC track even for fully silent sequences, for compatibility with Phase 1.6 and Phase 1.8 QA.

## Cache / invalidation

Each frame SHA-256 is included in the scene cache key; changing any frame, count, pattern, FPS or fit choice invalidates the corresponding sequence scene. The refreshed timeline also carries all asset hashes and must be regenerated after any asset changes.

## Limitations

- Sequence input uses FFmpeg's numbered image demuxer and FFV1 intermediate clips. Performance/disk use can be significant for thousands of 1080p images.
- No automatic frame interpolation, optical flow, sprite animation, image stabilization, FPS conversion, variable durations per frame, or cloud render.
- Existing projects without `image_sequence` retain their previous behavior.
- Windows 11 / FFmpeg 9 still requires on-device acceptance; Linux FFmpeg smoke tests do not guarantee OS-specific behavior.
