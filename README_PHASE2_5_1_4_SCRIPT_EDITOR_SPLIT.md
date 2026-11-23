# Phase 2.5.1.4 — Script Editor UI Split

## Mục tiêu
Tách riêng hai phần ở trang `/script-editor`:

1. **Thông tin chung của Script**
2. **Scenes**

Theo yêu cầu, hai phần này không còn nằm chung trong cùng một khối nội dung lớn nữa.

## Thay đổi chính

### UI `/script-editor`
- Tách khu vực bên phải thành **2 box độc lập**:
  - `3 · Thông tin chung của Script`
  - `4 · Scenes`
- Giữ nút **Save Changes** tại box thông tin chung.
- Khu vực **Scenes** vẫn có:
  - danh sách scene bên phải,
  - phần chi tiết scene ở giữa/bên trái.
- Khi chưa có script:
  - box 3 hiển thị notice tạo/mở script,
  - box 4 hiển thị notice rằng scene editor sẽ xuất hiện sau khi có script.

### JavaScript
- Tách hiển thị bằng hai container riêng:
  - `#editor-meta`
  - `#editor-scenes`
- Tái cấu trúc lại logic `draw()` để cập nhật 2 phần độc lập.
- Giữ cơ chế chọn scene bằng panel phải (`scene-nav`).

## Regression test
Cập nhật test GUI để xác nhận:
- có section `3 · Thông tin chung của Script`
- có section `4 · Scenes`
- có `editor-meta`
- có `editor-scenes`
- có `scene-nav`

## File thay đổi
- `src/ai_video_editor/api/static/script-editor.html`
- `tests/test_gui_phase246.py`
