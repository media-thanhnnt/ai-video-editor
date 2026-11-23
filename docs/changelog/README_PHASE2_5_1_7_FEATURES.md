# Phase 2.5.1.7 — Lớp chữ overlay, fit `auto`, 30 giọng Gemini, nối config "ma"

Đợt này xử lý 4 việc còn tồn từ [README_PHASE2_5_1_6_REVIEW_FIX.md](README_PHASE2_5_1_6_REVIEW_FIX.md) (mục A.5).

Kết quả kiểm thử: **294/294 test đạt**, gồm 15 test mới trong `tests/test_features_phase2517.py`. Trong số đó có 1 test end-to-end chạy FFmpeg thật, kiểm tra cùng lúc overlay, fit `auto`, ducking, loudness, retry và QA.

---

## 1. Lớp chữ overlay (text overlay)

### Vấn đề cũ
Renderer không có lớp chữ. Muốn có chữ thì phải ghép chữ vào ảnh trước khi render, nên chữ bị zoom và lia theo hình. Ngoài ra, phần "TEXT OVERLAY:" trong prompt còn bị gửi sang model tạo ảnh, khiến model cố vẽ chữ, thường là vẽ sai.

### Bây giờ
- Scene ảnh có thêm trường tùy chọn `overlay`: một file PNG trong suốt nằm trong `assets/overlays/`.
- Renderer **ghép overlay sau bước zoom/pan**. Hình minh họa vẫn chuyển động, còn chữ đứng yên và sắc nét.
- **Tạo thẻ chữ tại máy, không gọi API, không tốn phí.** Thẻ được vẽ từ phần `TEXT OVERLAY:` trong prompt của Script. Có 3 loại thẻ:
  - **Thẻ từ vựng:** khi có nhãn `Nghĩa:`. Thẻ gồm số thứ tự lấy từ "Từ số N" trong narration, furigana, từ cỡ lớn, romaji, từ gốc, nghĩa, câu ví dụ kèm romaji/EN/VI và các ghi chú.
  - **Thẻ quiz:** khi các mục có dạng `Thẻ N: từ → đáp án`. Thẻ chỉ hiện từ, **không hiện đáp án**; đáp án do lời đọc nói ra.
  - **Thẻ tiêu đề:** mọi trường hợp còn lại. Dòng đầu cỡ lớn, các dòng sau nhỏ hơn.
- **Bố cục:** khung hình ngang đặt khung chữ ở nửa trái; khung hình dọc (Shorts) đặt khung chữ ở phần dưới.
- **Font:** Yu Gothic (tiếng Nhật) và Segoe UI (tiếng Việt) trên Windows; Noto CJK hoặc DejaVu trên Linux. Có thể thay bằng font riêng của project: `assets/fonts/overlay_{jp|lat}_{bold|regular|italic}.ttf`.
- **Prompt gửi đi tạo ảnh** giờ tự cắt bỏ phần `TEXT OVERLAY:`.

### Cách dùng
- **GUI:** Render Studio → bấm **🔤 Tạo lớp chữ overlay** (đặt cạnh nút Auto-bind) → bấm **Tạo Storyboard & Editing Plan** → duyệt → render.
- **CLI:**
  ```powershell
  ai-video-editor overlay-generate learn_jp_002            # mọi scene có TEXT OVERLAY, tự gán vào manifest
  ai-video-editor overlay-generate learn_jp_002 --scenes 005,006 --no-bind
  ai-video-editor agent-bind learn_jp_002 --scene 005 --kind overlay --file assets/overlays/overlay_005_xxx.png
  ```
- **API:** `POST /api/projects/{id}/assets/overlays` với body `{"scene_ids": null, "bind": true}`. Route bind thủ công giờ cũng nhận `kind: "overlay"`.

### Chi tiết kỹ thuật
- **Storyboard:** `overlay` chỉ dùng được cho scene `image`, phải là file `.png` trong `assets/overlays/`.
- **Manifest:** thêm trường `overlay_file` (không bắt buộc). Nếu đã gán thì file phải tồn tại. `compile_storyboard` chép overlay sang storyboard.
- **Cache:** hash của overlay nằm trong timeline (`input_hashes`), trong cache key của scene và trong render identity. Đổi overlay thì scene được render lại; scene không có overlay giữ nguyên cache.
- **Ghép ảnh:** dùng alpha premultiplied trên numpy, độ chính xác float32.
- **Code:** `overlay.py` (mới); `renderer.py` (`_load_overlay`, `_composite`); `project.py`; `assets.py`; `timeline.py`; `agent/manifest.py`; `agent/asset_workflow.py`; `cli.py`; `api/app.py`; `api/static/render-studio.html`.

> Script `tools/compose_cards.py` trong thư mục YouTube giờ không còn cần nữa. Lưu ý thêm: nếu ảnh nền đã được ghép chữ sẵn từ trước, đừng gán thêm overlay, kẻo hiện chữ hai lần.

---

## 2. Ảnh lệch tỉ lệ không còn bị cắt: `image.fit_mode = "auto"`

- Ở chế độ `auto`, mỗi ảnh tĩnh được xét riêng:
  - Tỉ lệ ảnh lệch không quá 3% so với khung hình → dùng **`cover`**. Ví dụ: ảnh Gemini 16:9 sau bản sửa 2.5.1.6.
  - Lệch nhiều hơn → dùng **`blur_fill`**: giữ trọn ảnh, phần thừa hai bên là nền mờ của chính ảnh đó. Ví dụ: ảnh OpenAI 1536×1024 (3:2) trước đây bị cắt 15,6% chiều cao, giờ hiện đầy đủ.
- **Project mới** mặc định dùng `auto` (`config/default_project.json`). **Project cũ** giữ nguyên giá trị trong `config.json`; muốn dùng thì sửa thành `"fit_mode": "auto"`.
- **Sửa thêm một lỗi cũ:** trước đây đặt `blur_fill` thì **video clip và chuỗi ảnh bị lỗi**, vì `fit_filter` ném exception. Giờ `blur_fill` có filter FFmpeg tương đương (nền `gblur` + ảnh chính giữa). Với video và chuỗi ảnh, `auto` dùng `cover` như trước.
- **Code:** `renderer.py` (`_resolve_fit`), `sequence.py` (`fit_filter`), `project.py`.

---

## 3. Đủ 30 giọng Gemini TTS

- `GEMINI_VOICES` giờ có đủ 30 giọng dựng sẵn: Kore, Puck, Charon, Fenrir, Aoede, Zephyr, Leda, Orus, Callirrhoe, Autonoe, Enceladus, Iapetus, Umbriel, Algieba, Despina, Erinome, Algenib, Rasalgethi, Laomedeia, Achernar, Alnilam, Schedar, Gacrux, Pulcherrima, Achird, Zubenelgenubi, Vindemiatrix, Sadachbia, Sadaltager, Sulafat.
- 6 giọng cũ giữ nguyên thứ tự ở đầu danh sách. Giọng mặc định vẫn là Kore.
- `gemini_provider` dùng chung danh sách này, không còn danh sách gõ cứng thứ hai.
- **Code:** `api/voice_settings.py`, `api/gemini_provider.py`.

---

## 4. Config "ma": đã nối vào code hoặc đã gỡ

| Khóa | Trước | Bây giờ |
|---|---|---|
| `audio.music_enabled` | không dùng | `false` thì tắt toàn bộ `audio_tracks` (nhạc nền) mà không phải sửa storyboard |
| `audio.duck_depth_db`, `duck_attack_ms`, `duck_release_ms` | không dùng | **Ducking:** nhạc nền tự giảm `duck_depth_db` trong lúc có lời đọc, theo khoảng voice chính xác tới từng mẫu lấy từ timeline. Âm lượng giảm dần trong `attack` trước khi bắt đầu đọc và tăng lại trong `release` sau khi đọc xong. Các đoạn đọc sát nhau được gộp lại để nhạc không nhấp nhô. Đặt `0` để tắt. |
| `audio.music_fade_in_seconds`, `music_fade_out_seconds` | không dùng | Mỗi track fade-in khi bắt đầu và fade-out ở cuối video |
| `audio.loudness_target_lufs` | không dùng | **Chuẩn hóa độ to** theo EBU R128 (YouTube dùng −14 LUFS). Đo integrated loudness rồi áp **một mức gain cố định** (giới hạn ±20 dB), sau đó qua limiter `true_peak_max_dbtp`. Cách này không resample, nên không lệch sync. Đặt `null` để tắt. Kết quả ghi trong `reports/audio_report.json` → `loudness`. |
| `audio.true_peak_max_dbtp` | chỉ áp dụng khi có nhạc nền | Giờ luôn áp dụng, kể cả video chỉ có lời đọc |
| `subtitles.max_chars_per_line`, `max_lines` | không dùng | Cue phụ đề viết trực tiếp trong storyboard được ngắt dòng. Phần vượt `max_lines` dòng được tách thành các cue nối tiếp nhau, thời gian chia theo độ dài chữ. *Phụ đề nhập từ file SRT riêng (`subtitle_file`) giữ nguyên.* |
| `subtitles.safe_area_bottom_percent` | không dùng | Burn-in: `MarginV` của libass = 288 × % / 100 |
| `motion.zoom_factor`, `motion.easing` | không dùng (gõ cứng 1.05/1.10/1.18 và smoothstep) | Đọc từ config; `easing` nhận `ease_in_out` hoặc `linear` |
| `output.quality` | không dùng (CRF cứng 18) | `very_high`=15, `high`=18, `medium`=21, `low`=24 |
| `retry.render_max_attempts`, `backoff_seconds` | không dùng | Khi bước encode lỗi, render được thử lại (scene đã xong được dùng lại), chờ theo `backoff_seconds` giữa các lần. Bước kiểm tra dữ liệu không bao giờ được thử lại. |
| `planning.use_ai` | không dùng | `false` thì nút **Tạo Storyboard & Editing Plan** dùng plan theo luật, dù đã có AI Scene Plan được duyệt |
| `audio.music_base_gain_db` | không dùng | **Gỡ khỏi config mặc định.** Khóa này trùng với `audio_tracks[].volume`; áp thêm sẽ làm nhạc nhỏ đi 16 dB ở mọi project cũ. |
| `timing.allow_approximate_timing` | không dùng | **Gỡ.** Timeline luôn chính xác tới từng frame/sample, theo thiết kế. |
| `planning.max_revisions` | không dùng | **Gỡ.** Plan AI không bao giờ được tự sửa lại. |

**Tương thích ngược**
- Mọi khóa trên đều **không bắt buộc**. Thiếu khóa nào thì dùng hành vi cũ.
- Các khóa đã gỡ, nếu còn trong `config.json` của project cũ, sẽ bị bỏ qua.
- Cache key và render identity **chỉ thay đổi khi giá trị khác mặc định**, nên project cũ không phải render lại video chỉ vì nâng cấp.

**Thay đổi bạn sẽ nghe hoặc thấy ở project cũ khi render lại**
- **Âm thanh** (chỉ mux lại audio, không encode lại video): âm lượng được chuẩn hóa về −14 LUFS; nhạc nền có ducking và fade (nếu `config.json` của project vẫn giữ các khóa mặc định đó); limiter cũng áp dụng cho video chỉ có lời đọc.
- **Phụ đề burn-in:** video được encode lại một lần, vì có `MarginV` và ngắt dòng mới.

**Code:** `project.py` (`_validate_optional_settings`, `QUALITY_CRF`, `DEFAULT_ZOOM`), `renderer.py`, `audio.py` (`duck_filter`, `measure_loudness_gain`, `wrap_cue`, `_paged_cues`, mux 2 bước), `api/workflow.py`, `config/default_project.json`.

---

## File thay đổi

| File | Loại |
|---|---|
| `src/ai_video_editor/overlay.py` | mới |
| `src/ai_video_editor/renderer.py`, `audio.py`, `project.py`, `assets.py`, `timeline.py`, `sequence.py`, `cli.py` | sửa |
| `src/ai_video_editor/agent/manifest.py`, `agent/asset_workflow.py` | sửa |
| `src/ai_video_editor/api/app.py`, `api/workflow.py`, `api/voice_settings.py`, `api/gemini_provider.py`, `api/static/render-studio.html` | sửa |
| `config/default_project.json` | sửa |
| `tests/test_features_phase2517.py` | mới |
| `README_PHASE2_5_1_7_FEATURES.md` | mới |

Chạy test an toàn (không gọi API thật):

```powershell
$env:PYTHON_KEYRING_BACKEND = "keyring.backends.fail.Keyring"
Remove-Item Env:OPENAI_API_KEY, Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
py -m unittest discover -s tests -v
```

Sau khi cập nhật code: khởi động lại `ai-video-editor ui` rồi tải lại trang bằng `Ctrl+Shift+R`.
