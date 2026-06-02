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

### Classify Document Or Photo

```text
POST /api/v1/classifications
Content-Type: multipart/form-data
```

Form fields:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `file` | file | yes | Image cần classify. |

Pipeline:

```text
image -> RGB -> resize 224x224 -> ImageNet normalization -> ONNX Runtime -> softmax -> class + confidence
```

Class mapping:

| Class ID | Label |
| --- | --- |
| `0` | `document` |
| `1` | `photo` |

Response:

```json
{
  "filename": "input.png",
  "predicted_class_id": 0,
  "predicted_class": "document",
  "confidence": 0.9821,
  "probabilities": [
    {
      "class_id": 0,
      "label": "document",
      "confidence": 0.9821
    },
    {
      "class_id": 1,
      "label": "photo",
      "confidence": 0.0179
    }
  ]
}
```

cURL example:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/classifications" \
  -F "file=@/path/to/input.png"
```

### Restore Document

```text
POST /api/v1/restorations
Content-Type: multipart/form-data
```

Form fields:

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `file` | file | yes | - | Degraded image (`png`, `jpg`, `jpeg`, `webp`, `tif`, `tiff`, `bmp`). |
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
  -F "file=@/path/to/degraded-image.jpeg" \
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
upload degraded image -> validate -> save upload -> call remote Gradio AI restoration API -> save restored image -> return output URL
```

Remote AI restoration Space mặc định là `elnino1512/AI-Document-image-enhencement`, API name mặc định là `/restore_image`.
# GraThe-Backend
