# Phase 1.1 — Functional contract

Nguồn thiết kế: README AI Video Editor v1.1, mục 5.1, 6.0, 6.1, 6.2 và 12.

## API

- `ProjectManager(projects_dir, default_config)`
- `create_project(project_id, title, config_overrides=None) -> Project`: tạo project mới từ config mặc định bằng staging folder và rename. Trùng ID: báo lỗi, không ghi đè.
- `load_project(project_id) -> Project`: đọc và xác thực config/storyboard/job state; kiểm tra đường dẫn thuộc project.
- `save_project(project_id, config=None, storyboard=None) -> Project`: validate trước khi ghi; atomic replace từng JSON.
- `get_project_status(project_id) -> dict`: đọc trạng thái hiện tại (`CREATED`).
- `list_projects() -> list[str]`: lấy danh sách project trong local folder.
- `validate_config`/`validate_storyboard`: schema version 1.1 và quy tắc cốt lõi.

## Quy tắc

- JSON UTF-8; path trong JSON dùng `/`, tương đối với project.
- project_id chỉ gồm ASCII letters, numbers, `_`, `-`; scene_id dùng letters/numbers/`_`.
- `order` phải khớp vị trí scene trong mảng (1-based), scene_id không trùng.
- MVP Phase 1 dùng `voice_mode=per_scene`; `single_track` để Phase 3.
- Không tự đọc hay di chuyển tài nguyên từ ngoài project; kiểm tra symlink khi load/save.
- `schema_version != "1.1"` bị từ chối, chưa có migration.
- `config.json` giữ nguyên nhóm trường gốc của thiết kế v1.1.
- Không kiểm tra nội dung ảnh, audio, font hoặc tồn tại của media ở Phase 1.1.

## Hạn chế chủ động

- Không có transactional write đồng thời cho config và storyboard (chỉ atomic từng file); đa tiến trình ghi đồng thời chưa hỗ trợ.
- Không tự động phục hồi project bị chỉnh sửa thủ công ở trạng thái không nhất quán.
- `job_state.json` là scaffold, chưa hỗ trợ chuyển trạng thái ngoại trừ CREATED.
- Phần validation config tập trung vào trường quan trọng; các giới hạn số học chuyên sâu sẽ hoàn thiện cùng Timeline Engine ở 1.3.
