# Phase 2.5.1.6 — Code review & sửa lỗi

Tài liệu này gồm hai phần:

- **Phần A:** những gì đã chỉnh sửa trong đợt review này.
- **Phần B:** mô tả lại toàn bộ source code của AI Video Editor (kiến trúc, từng module, dữ liệu, API, GUI, test).

Kết quả kiểm thử sau khi sửa: **279/279 test đạt** (`py -m unittest discover -s tests`, Windows 11, Python 3.14, FFmpeg 9.0.2). Lần chạy này đã tắt keyring và biến môi trường API key, xem mục A.4.

---

## A. Những gì đã chỉnh sửa

### A.1 Trạng thái trước khi sửa

Bộ test có **3 test lỗi**, kèm thêm vài lỗi logic tìm được khi đọc code:

| Test | Kết quả | Nguyên nhân |
|---|---|---|
| `test_agent_script_phase22.test_offline_cli_integration` | ERROR | CLI crash `UnicodeEncodeError: 'cp932'` khi in tiếng Việt ra stdout đã bị chuyển hướng. Đây là lỗi thật của sản phẩm (xem lỗi 1). |
| `test_cli_phase251.test_no_key_no_quota` | FAIL | Test đọc **keyring thật** của máy. Máy có API key nên test **gửi một request TTS thật, có tính phí** (xem lỗi 2). |
| `test_help_layout_phase252.test_script_metadata_separated_from_scene_panel` | FAIL | Test còn kiểm tra cấu trúc HTML cũ, trước khi Phase 2.5.1.4 tách trang Script Editor thành hai box. |

### A.2 Danh sách lỗi đã sửa

| # | Mức độ | Lỗi | Cách sửa | File |
|---|---|---|---|---|
| 1 | **Cao** | **CLI crash khi in Unicode ra pipe hoặc file.** Khi stdout không phải console (bị chuyển hướng sang file hoặc pipe, chẳng hạn log render của GUI), Python dùng code page ANSI (cp932, cp1252…). Chỉ cần in một ký tự tiếng Việt hoặc tiếng Nhật không nằm trong code page đó là lệnh bị dừng với `UnicodeEncodeError`. | `cli.main()` gọi `_utf8_stdio()`: nếu stdout/stderr chưa phải UTF-8 thì chuyển sang UTF-8 (`errors='replace'`). Ngoài ra, subprocess render của GUI (`render_cli`) chạy với `PYTHONIOENCODING=utf-8` và `PYTHONUTF8=1` để khớp với file log đang được đọc bằng UTF-8. | `cli.py`, `api/workflow.py` |
| 2 | **Cao** | **Test gọi API trả phí thật.** `test_no_key_no_quota` không mock `get_key`, nên trên máy đã lưu key thì lệnh `asset generate-voice … --yes` gọi provider thật. Như vậy file test ghi "all paid API operations mocked" là sai. | Test patch `get_key → None`, đồng thời mock cả `generate_openai_asset` và `gemini_asset`, rồi khẳng định hai hàm này không được gọi. | `tests/test_cli_phase251.py` |
| 3 | **Cao** | **Gemini tạo ảnh vuông.** Request tạo ảnh không gửi tỉ lệ khung hình, nên Gemini trả ảnh 1:1. Với `fit_mode=cover`, khung 16:9 phải cắt khoảng **44% chiều cao**, nhân vật dễ bị mất đầu hoặc chân. | Thêm `generationConfig.imageConfig.aspectRatio`, lấy theo `config.output` của project qua hàm `output_aspect_ratio()`: 1920×1080 → `16:9`, 1080×1920 → `9:16`, chọn tỉ lệ hỗ trợ gần nhất. | `api/gemini_provider.py`, `agent/asset_workflow.py`, `api/app.py`, `cli_assets.py` |
| 4 | Trung bình | **OpenAI luôn tạo ảnh ngang 1536×1024**, kể cả với project dọc (Shorts 9:16). | `openai_image_size()` chọn `1536x1024` (ngang), `1024x1536` (dọc) hoặc `1024x1024` (vuông) theo tỉ lệ của project. | `agent/asset_workflow.py` + các chỗ gọi |
| 5 | Trung bình | **Chỉ dẫn giọng đọc không được gửi đi.** `make_prompt_pack` đã tạo `audio_instructions`, nhưng TTS không hề nhận được. | Gửi `instructions` cho model OpenAI `gpt-4o*-tts`. Không gửi cho `tts-1`/`tts-1-hd` vì hai model này không hỗ trợ, và không gửi cho provider `openai_compatible` vì có thể bị từ chối field lạ. Gemini không đổi, vì chèn chỉ dẫn vào text có nguy cơ bị đọc thành tiếng. | `agent/asset_workflow.py`, `api/app.py`, `cli_assets.py` |
| 6 | Trung bình | **Gemini PCM bị gán cứng 24 kHz.** Nếu Gemini trả `audio/L16;rate=…` với tần số khác, file WAV sẽ sai tốc độ và cao độ. | Đọc `rate=` từ mimeType (mặc định 24000, chấp nhận 8–192 kHz). | `api/gemini_provider.py` |
| 7 | Trung bình | **Code gọi provider bị chép 3 lần** trong route sync, job và batch, với giá trị voice mặc định không thống nhất (`default_voice` ở route sync, `'alloy'` cứng ở job và batch). | Gộp vào hàm `_call_asset_provider()` dùng chung, nên cả 3 route nhận cùng tham số: voice, tỉ lệ ảnh, chỉ dẫn giọng. | `api/app.py` |
| 8 | Thấp | **CLI batch in traceback khi hết giới hạn giữa chừng.** `reserve()` ném `ValueError`, nhưng `main()` chỉ bắt `ValidationError`. | Chuyển lỗi đó thành `ValidationError`, nên CLI in `ERROR: …`, trả exit 1 và không gọi API. | `cli_assets.py` |
| 9 | Thấp | **Batch API trả HTTP 500** khi Script lỗi hoặc thiếu, vì `make_prompt_pack` nằm ngoài khối `try`. | Bọc lại và trả HTTP 422 kèm thông báo. | `api/app.py` |
| 10 | Thấp | **Generate Scene Plan không kiểm tra job đang chạy**, trong khi Generate Script có kiểm tra. Hệ quả là có thể sinh plan mới trong lúc render. | Thêm kiểm tra `jobs.active`, trả HTTP 409. | `api/app.py` |
| 11 | Thấp | `update_scene_plan` liệt kê `render_checkpoint.json` ở thư mục gốc project, nhưng file thật nằm ở `reports/`. Vì vậy mục này không bao giờ khớp. | Bỏ mục thừa và ghi chú lý do: checkpoint đã tự mất hiệu lực qua render identity. | `api/app.py` |
| 12 | Thấp | Test layout Script Editor đã lỗi thời. | Kiểm tra theo cấu trúc mới: `#editor-meta` đứng trước `#editor-scenes`, hai phần nằm ở hai `<section>` riêng, và có `scene-nav`. | `tests/test_help_layout_phase252.py` |

### A.3 Test mới: `tests/test_review_fixes_phase2516.py`

Có 9 test, tất cả đều mock provider và không dùng mạng hay keyring:

- Tỉ lệ ảnh theo project: 16:9, 9:16, 1:1, và mặc định khi config thiếu.
- Kích thước ảnh OpenAI theo hướng khung hình.
- Gemini nhận `imageConfig.aspectRatio`; OpenAI nhận đúng `size`.
- Gemini PCM 48 kHz tạo ra WAV 48 kHz.
- `instructions` chỉ được gửi cho `gpt-4o-mini-tts`, không gửi cho `tts-1` hay provider compatible.
- `_utf8_stdio()` giúp in "Đặc tả 仕様" qua stream cp932 mà không crash.
- Hết giới hạn giữa batch thì ra `ValidationError` và không gọi API.
- Subprocess render của GUI có `PYTHONIOENCODING=utf-8`.

### A.4 Cách chạy test an toàn (không bao giờ gọi API thật)

Một số test cũ vẫn dùng `get_key` thật. Hãy luôn chạy test với keyring bị tắt và không có biến môi trường API key:

```powershell
$env:PYTHON_KEYRING_BACKEND = "keyring.backends.fail.Keyring"
Remove-Item Env:OPENAI_API_KEY, Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
py -m unittest discover -s tests -v
```

### A.5 Phát hiện nhưng chưa sửa (cần quyết định hoặc ngoài phạm vi)

- **Config không được dùng:** `audio.music_enabled`, `duck_*`, `music_fade_*`, `loudness_target_lufs`, `subtitles.max_chars_per_line`, `max_lines`, `safe_area_bottom_percent`, `planning.max_revisions`, `retry.*`. Không module nào đọc các khóa này, nên người dùng dễ tưởng đã có ducking, chuẩn hóa LUFS hay retry.
- **Ảnh OpenAI 3:2 vẫn bị cắt khoảng 15,6% chiều cao khi đưa vào khung 16:9**, vì gpt-image không có kích thước 16:9. Nếu cần giữ trọn ảnh, có thể dùng `fit_mode: contain` hoặc thêm chế độ nền mờ cho ảnh tĩnh.
- **Danh sách voice Gemini chỉ có 6 giọng**, trong khi Gemini TTS hiện có nhiều giọng hơn.
- **Không có lớp chữ (text overlay) trong renderer.** Các video học từ vựng đang phải ghép chữ vào ảnh trước khi render, nên chữ bị zoom và lia theo hình.
- **Thông tin phiên bản cũ:** `README.md` mục 13 vẫn ghi `pyproject.toml` là 2.4.7, trong khi hiện tại là 2.5.1.3. `/api/health` cũng báo `2.5.1.3`. Đợt này không đổi số phiên bản.
- **Thay đổi chưa commit:** Git hiện chỉ có commit tới phase 2.3. Toàn bộ phase 2.4 đến 2.5.1.x vẫn là thay đổi chưa commit.

### A.6 File đã thay đổi trong đợt này

| File | Loại |
|---|---|
| `src/ai_video_editor/cli.py` | sửa |
| `src/ai_video_editor/cli_assets.py` | sửa |
| `src/ai_video_editor/agent/asset_workflow.py` | sửa |
| `src/ai_video_editor/api/app.py` | sửa |
| `src/ai_video_editor/api/gemini_provider.py` | sửa |
| `src/ai_video_editor/api/workflow.py` | sửa |
| `tests/test_cli_phase251.py` | sửa |
| `tests/test_help_layout_phase252.py` | sửa |
| `tests/test_review_fixes_phase2516.py` | mới |
| `README_PHASE2_5_1_6_REVIEW_FIX.md` | mới (tài liệu này) |

**Sau khi cập nhật:** dừng `ai-video-editor ui`, chạy lại, rồi tải lại trang bằng `Ctrl+Shift+R` thì server mới dùng code đã sửa.

---

## B. Mô tả toàn bộ source code

### B.1 Tổng quan

AI Video Editor là một ứng dụng chạy hoàn toàn trên máy (local). Nó dựng video MP4 (H.264/AAC) từ một storyboard gồm ảnh tĩnh, chuỗi ảnh hoặc video clip, cộng với lời đọc của từng scene. Ứng dụng có 4 lớp:

```text
┌──────────────────────────── GUI (FastAPI + HTML tĩnh, 127.0.0.1:8765) ────────────────────────────┐
│ Dashboard · Script Editor · Scene Planner · Asset Workspace · Render Studio · API Settings · Help │
└──────────────┬───────────────────────────────────────────────────────────────────┬──────────────┘
               │ api/app.py (REST)                                                  │ subprocess
┌──────────────▼──────────── Agent (AI, có duyệt) ─────────────┐          ┌────────▼─────────┐
│ script_generator → scene_planner → manifest → asset_workflow │          │ CLI (cli.py,     │
│ provider_settings · gemini_provider · limits · voice_settings│◀────────▶│ cli_assets.py)   │
└──────────────┬───────────────────────────────────────────────┘          └────────┬─────────┘
               │ storyboard.json + editing_plan.json                                │
┌──────────────▼──────────────────────── Core (tất định, offline) ──────────────────▼─────────┐
│ project → assets → planner → timeline → renderer → audio → qa      (FFmpeg/FFprobe, OpenCV)  │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

Luồng đầy đủ: **Yêu cầu → Script → Scene Plan → Asset (ảnh/giọng) → Manifest/Bind → Storyboard → Editing Plan → Render → Audio → QA**.

- Bước nào tạo bản nháp thì phải được người dùng **duyệt**. Bản duyệt gắn với hash SHA-256 của nội dung, nên nội dung đổi là phải duyệt lại.
- Chỉ những bước người dùng bấm rõ ràng mới gọi API trả phí. Render luôn chạy tại máy.

### B.2 Cấu trúc thư mục

```text
ai-video-editor/
├── src/ai_video_editor/       # mã nguồn (package)
│   ├── *.py                   # Core + CLI
│   ├── agent/                 # tầng AI có duyệt
│   └── api/                   # web server + GUI tĩnh (api/static/*.html)
├── tests/                     # unittest (provider được mock)
├── config/default_project.json, config/providers.json
├── examples/                  # storyboard, script, scene plan mẫu
├── frontend/                  # mã React/Vite (không bắt buộc; GUI dùng HTML tĩnh)
├── scripts/create_demo_assets.py
└── projects/<id>/             # dữ liệu từng project (không commit)
```

### B.3 Core: dựng video tất định (`src/ai_video_editor/`)

| Module | Vai trò | Hàm/lớp chính |
|---|---|---|
| `project.py` | Tạo, đọc, ghi project; kiểm tra cấu trúc `config.json` và `storyboard.json`; ghi JSON nguyên tử; chặn đường dẫn thoát ra ngoài project. | `ProjectManager`, `Project`, `validate_config`, `validate_storyboard`, `validate_sequence`, `atomic_write_json`, `check_relpath` |
| `assets.py` | Dùng FFprobe/FFmpeg kiểm tra media thật: kích thước ảnh, số mẫu PCM thực của voice, video, chuỗi frame, SHA-256, ước tính dung lượng đĩa. | `validate_assets`, `get_image_info`, `get_audio_info`, `get_video_info`, `compute_input_hashes` |
| `planner.py` | Editing plan theo luật (style + seed → motion, intensity, transition cho từng scene), kiểm tra plan, cache key, cổng duyệt. | `get_default_editing_plan`, `validate_editing_plan`, `plan_cache_key`, `require_approved_plan` |
| `timeline.py` | Dựng timeline bằng số nguyên frame và sample. Thời lượng scene = đệm đầu + voice + đệm cuối; overlap transition; intro/outro. | `build_timeline`, `validate_timeline` |
| `renderer.py` | Render từng scene thành clip FFV1. Zoom/Pan tính từ chỉ số frame tuyệt đối bằng biến đổi affine ngược + Lanczos4 trên ảnh supersample. Transition dùng `xfade` (`crossfade` = `fade`, `dissolve` = hòa hạt ngẫu nhiên). Có checkpoint, resume và render lock. | `render_project`, `motion_frame`, `compute_render_identity`, `verify_render_checkpoint` |
| `sequence.py` | Hỗ trợ chuỗi ảnh đánh số: bộ lọc fit, danh sách frame. | `fit_filter`, `sequence_manifest` |
| `audio.py` | Ghép voice từng scene thành `voice_mix.wav` chính xác tới từng mẫu, trộn nhạc nền và âm thanh gốc của clip, giới hạn đỉnh, phụ đề SRT hoặc in thẳng vào video, mux thành `final.mp4`. | `build_voice_mix`, `prepare_subtitles`, `mux_project_audio` |
| `qa.py` | Kiểm tra chỉ đọc: assets, độ mới của plan/timeline, checkpoint, codec, số frame/sample. Chế độ `--deep` thêm cảnh báo khung đen và khoảng lặng. | `qa_project` |
| `ffmpeg_support.py` | Kiểm tra phiên bản và khả năng của FFmpeg trước khi chạy. | `check_ffmpeg` |
| `cli.py` | Toàn bộ lệnh CLI (`init`, `compose`, `agent-*`, `provider`, `asset`, `ui`…). Giờ ép stdout sang UTF-8. | `main` |
| `cli_assets.py` | Dịch vụ CLI không cần web server: tạo ảnh/voice, batch, xác nhận trả phí, quản lý provider. Dùng chung logic với GUI. | `generate`, `planned_scenes`, `provider_command` |

### B.4 Agent: tầng AI có duyệt (`agent/`)

| Module | Vai trò |
|---|---|
| `script_generator.py` | Schema và kiểm tra Script (`title`, `language`, `style`, `target_duration_seconds`, `summary`, `script`, `scenes[]`), gọi OpenAI Responses API, lưu `agent_script_draft.json`, duyệt bằng hash, xuất `agent_brief_generated.json`. |
| `scene_planner.py` | Đề xuất cho từng scene: `media_type`, `asset_prompt_en`, motion, intensity, transition và lý do. Hỗ trợ instructions tùy chỉnh (Phase 2.5.1.5), duyệt, xuất `agent_scene_brief.json`, và `apply_scene_plan` sinh editing plan `ai_assisted`. |
| `manifest.py` | `agent_manifest.json`: gán file media và voice cho từng scene, kiểm tra đuôi file và đường dẫn, duyệt, `compile_storyboard` thành `storyboard.json`. |
| `asset_workflow.py` | Gói prompt (ảnh/video/voice + chỉ dẫn giọng), xuất Markdown, gọi OpenAI Images/Speech, lưu asset vào `assets/` kèm nhật ký `generated_assets.json`. Mới thêm: `output_aspect_ratio`, `openai_image_size`, `voice_instructions`. |
| `tool_registry.py` | Danh sách công cụ cố định mà agent được phép dùng; không chạy lệnh shell từ model. |

### B.5 API và GUI (`api/`)

| Module | Vai trò |
|---|---|
| `app.py` | FastAPI chỉ nghe trên loopback, có `TrustedHostMiddleware` và chặn request ghi từ origin khác. Gồm các route project, script, scene plan, asset (tạo đơn, job, batch, upload, bind), render (prepare, approve, start, output) và phục vụ các trang HTML. |
| `provider_settings.py` | Danh mục provider/model (`config/providers.json`), chọn provider theo tác vụ, lưu key trong OS keyring (hoặc biến môi trường `OPENAI_API_KEY`/`GEMINI_API_KEY`), thêm/xóa provider `openai_compatible` (chỉ HTTPS). |
| `gemini_provider.py` | Gọi Gemini REST: text JSON (script, scene plan), ảnh (giờ có `aspectRatio`), TTS WAV (giờ đọc đúng sample rate). |
| `jobs.py` | Quản lý job chạy nền trong bộ nhớ: 2 worker, tối đa 1 job đang chạy mỗi project. Có tiến độ, nhưng mất khi khởi động lại server. |
| `limits.py` | Giới hạn số lần gọi API mỗi ngày (UTC) cho từng project; giữ chỗ trước mỗi lần gọi. |
| `voice_settings.py` | Danh sách voice theo provider và voice đã chọn của project (`voice_preferences.json`). |
| `workflow.py` | Logic dùng chung cho GUI: tạo manifest, tự động bind asset vừa tạo, chuẩn bị storyboard và editing plan, duyệt plan, chạy `compose --deep` dưới dạng subprocess kèm tiến độ theo checkpoint. |
| `static/*.html`, `sidebar.js/css` | Các trang GUI (HTML/JS thuần, không cần build) và sidebar dùng chung trong Shadow DOM. |

### B.6 File dữ liệu trong `projects/<id>/`

| File | Tạo bởi | Nội dung |
|---|---|---|
| `config.json`, `storyboard.json`, `job_state.json` | `init` / `agent-compile` | Cấu hình output, danh sách scene cho Core |
| `agent_script_draft.json` | Script Editor / `agent-script` | Script, trạng thái, hash duyệt, token đã dùng |
| `agent_brief_generated.json` | Export Brief | Brief tối giản lấy từ Script |
| `agent_scene_plan.json`, `agent_scene_brief.json` | Scene Planner | Đề xuất scene và brief cho asset |
| `agent_manifest.json` | Render Studio / `agent-init` | Gán media và voice cho từng scene |
| `generated_assets.json` | Tạo asset | Nhật ký file sinh ra (provider, model, hash prompt) |
| `api_request_limits.json`, `api_request_usage.json` | Asset Workspace | Giới hạn và số lần đã dùng trong ngày |
| `voice_preferences.json` | Chọn voice | Voice theo provider |
| `editing_plan.json`, `timeline.json` | Plan / compose | Quyết định dựng và timeline frame/sample |
| `script_revision_pending.json`, `history/` | Sửa Script hoặc Scene Plan | Đánh dấu phải làm lại các bước sau; bản lưu cũ |
| `work/`, `reports/`, `output/`, `logs/` | Render | Cache, checkpoint, báo cáo, `final.mp4`, log GUI |

### B.7 Nhóm route HTTP (đều nằm dưới `/api`)

- **Provider:** `GET/PUT /provider-settings`, key, provider, model.
- **Project:** `GET/POST /projects`, `GET /projects/{id}`.
- **Script:** `GET/PUT /script`, `generate`, `editable-export`, `import`, `approve`, `export`.
- **Scene plan:** `GET/PUT /scene-plan`, `prompt-preview`, `generate`, `approve`, `export`.
- **Asset:** `asset-prompts(.md)`, `assets/voice-settings`, `assets/generate`, `generate-job`, `batch-job`, `status`, `limits`, `manifest`, `upload`, `auto-bind`, `bind`.
- **Job và render:** `jobs/{job_id}`, `render/prepare`, `render/approve-plan`, `render/start`, `render/output`.

Mọi route trả phí đều cần `"confirmed": true`. Tài liệu tương tác có tại `/api/docs`.

### B.8 Kiểm thử

Bộ test gồm 38 file, 279 test, chia theo phase:

- Core: `test_assets`, `test_timeline`, `test_renderer`, `test_audio`, `test_qa`, `test_regressions_110*`…
- Agent: `test_agent*`.
- GUI: `test_gui_phase24*` và các file hotfix.
- CLI: `test_cli_phase251`.
- Review này: `test_review_fixes_phase2516`.

Test FFmpeg chạy thật. Các test gọi provider dùng mock; hãy luôn chạy theo hướng dẫn ở A.4.
