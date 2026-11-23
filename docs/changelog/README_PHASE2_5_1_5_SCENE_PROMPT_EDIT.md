# Phase 2.5.1.5 — Scene Planner Prompt Preview & Edit Approved Plan

## Chức năng

- Màn hình `/scene-planner` hiển thị **Prompt gửi LLM** được xây dựng từ provider hiện được cấu hình. Có thể sửa phần `instructions` trước khi bấm **Generate Scene Plan**, hoặc khôi phục bằng **Khôi phục prompt mặc định**.
- Kịch bản đã duyệt được trình bày riêng trong mục JSON read-only. API OpenAI Responses gửi hai trường `instructions` và `input` riêng; Gemini gộp `instructions` + `INPUT:\n` + JSON vào một text part. Bộ schema đầu ra vẫn được API yêu cầu như trước.
- Nút Save Changes làm việc với plan cả khi trạng thái `SCENE_PLAN_APPROVED`.
- Nếu nội dung giống bản lưu: approval và các file phía sau được giữ nguyên.
- Nếu nội dung khác và có Asset Brief/Manifest/Storyboard/Editing Plan: API từ chối với HTTP 409 nếu chưa xác nhận. GUI hỏi lại rồi mới gửi `replace_downstream: true`.
- Khi xác nhận sửa, các file phụ thuộc hiện có được sao lưu trong `projects/<project_id>/history/scene_revision_*/`, sau đó vô hiệu hóa. `storyboard.json` được đặt về mẫu rỗng hợp lệ để project vẫn mở được. Kịch bản và media gốc không bị xóa; MP4 cũ không bị xóa nhưng được đánh dấu thuộc revision trước.
- Plan mới có `status=AWAITING_SCENE_PLAN_APPROVAL`, phải Approve lại và xuất lại brief, bind/compile/prepare/render lại.
- Các lệnh CLI cũ không thay đổi.

## Hướng dẫn

1. Mở `/scene-planner` và chọn project có Script đã duyệt.
2. Xem phần **Prompt gửi LLM** và **Kịch bản JSON gửi kèm**. Sửa prompt nếu cần.
3. Bấm **Generate Scene Plan** (có thể phát sinh phí API). Chỉnh từng scene trong **4 · Scene Decisions**.
4. Với Scene Plan đã được Approve: chỉnh các trường rồi bấm **Save Changes**. Nếu có các file downstream, GUI sẽ yêu cầu xác nhận lưu bản cũ vào `history/`.
5. Approve lại, Export Asset Brief, kiểm tra media và dựng lại.

## API mới hoặc mở rộng

- `GET /api/projects/{id}/scene-plan/prompt-preview` — trả về prompt mặc định, Script JSON, provider/model và loại request; không trả về API key.
- `POST /api/projects/{id}/scene-plan/generate` — thêm tùy chọn `instructions` (10–12000 ký tự), không thay đổi CLI.
- `PUT /api/projects/{id}/scene-plan` — thêm `replace_downstream` (mặc định `false`).

## Kiểm thử

```powershell
py -m unittest discover -s tests -p "test_scene_prompt_edit_phase2515.py" -v
py -m unittest discover -s tests -p "test_gui_phase24*.py" -v
py -m unittest discover -s tests -p "test_agent*.py" -v
```

Các kiểm thử dùng mock với OpenAI/Gemini, chưa xác nhận API có phí hoặc browser trên Windows.
