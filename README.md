# Document Image Enhancement Backend

FastAPI backend cho project restore document image. Backend nhận file PDF hoặc image, render từng trang nếu là PDF, gọi module AI trong `../AI-Document-image-enhencement`, rồi trả về danh sách ảnh restored.

## Cấu trúc

```text
backend/
  app/
    api/v1/              # REST endpoints
    core/                # config, settings
    schemas/             # response/request schemas
    services/            # business logic, AI adapter, PDF/image loader
    utils/               # helper nhỏ
    main.py              # FastAPI app
  AI_models/             # model/assets backend-local nếu cần
  storage/
    uploads/             # file input tạm
    restored/            # output public qua /restored
  requirements.txt
```

## Cài đặt

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Chạy development với auto reload

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

Health check:

```text
GET http://127.0.0.1:8000/api/v1/health
```

## API

### Restore Document

```text
POST /api/v1/restorations
Content-Type: multipart/form-data
```

Form fields:

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `file` | file | yes | - | PDF hoặc image (`png`, `jpg`, `jpeg`, `webp`, `tif`, `tiff`, `bmp`). |
| `patch_size` | int | no | `512` | Kích thước patch. Hỗ trợ `256`, `384`, `512`, `768`. |
| `batch_size` | int | no | `4` | Số patch xử lý mỗi batch. CPU/máy yếu nên để thấp. |
| `threshold` | float | no | `0.5` | Ngưỡng binarize, dùng khi `binarize_output=true`. |
| `binarize_output` | bool | no | `true` | Trả ảnh trắng/đen nếu bật. |
| `overlap` | bool | no | `true` | Patch overlap 50% để reconstruction mượt hơn. |

Response:

```json
{
  "request_id": "7d7d8b1f2d9c4a2a9f935e11a2a03d16",
  "input_filename": "document.pdf",
  "input_type": "pdf",
  "total_pages": 2,
  "outputs": [
    {
      "page": 1,
      "filename": "page-001.png",
      "url": "/restored/7d7d8b1f2d9c4a2a9f935e11a2a03d16/page-001.png"
    },
    {
      "page": 2,
      "filename": "page-002.png",
      "url": "/restored/7d7d8b1f2d9c4a2a9f935e11a2a03d16/page-002.png"
    }
  ]
}
```

cURL example:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/restorations" \
  -F "file=@/path/to/document.pdf" \
  -F "patch_size=512" \
  -F "batch_size=4" \
  -F "threshold=0.5" \
  -F "binarize_output=true" \
  -F "overlap=true"
```

Ảnh output được serve theo URL trong response, ví dụ:

```text
http://127.0.0.1:8000/restored/{request_id}/page-001.png
```

## Ghi chú luồng xử lý

```text
upload PDF/image -> validate -> save upload -> load image pages -> lazy-load AI module -> restore từng page -> save PNG -> return output URLs
```

Model trong `AI-Document-image-enhencement/app.py` sẽ được load lần đầu khi gọi restore, không load ngay lúc start server.
# GraThe-Backend
