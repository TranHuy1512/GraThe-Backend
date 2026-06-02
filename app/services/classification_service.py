from io import BytesIO

import numpy as np
import onnxruntime as ort
from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.schemas.classification import ClassificationResponse, ClassProbability


LABELS = {
    0: "document",
    1: "photo",
}


class ClassificationService:
    def __init__(self) -> None:
        self._session: ort.InferenceSession | None = None
        self._input_name: str | None = None

    async def classify_upload(self, file: UploadFile) -> ClassificationResponse:
        if not file.filename:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename.")

        content = await file.read()
        max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File is larger than {settings.MAX_UPLOAD_MB} MB.",
            )

        try:
            image = Image.open(BytesIO(content)).convert("RGB")
        except UnidentifiedImageError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported image file. Please upload a valid image.",
            ) from exc

        probabilities = self.classify_image(image)
        predicted_class_id = int(np.argmax(probabilities))
        predicted_class = LABELS[predicted_class_id]
        confidence = float(probabilities[predicted_class_id])

        return ClassificationResponse(
            filename=file.filename,
            predicted_class_id=predicted_class_id,
            predicted_class=predicted_class,
            confidence=confidence,
            probabilities=[
                ClassProbability(class_id=class_id, label=LABELS[class_id], confidence=float(probability))
                for class_id, probability in enumerate(probabilities)
            ],
        )

    def classify_image(self, image: Image.Image) -> np.ndarray:
        session = self._get_session()
        input_tensor = self._preprocess_image(image)
        raw_output = session.run(None, {self._input_name: input_tensor})[0][0]
        return self._softmax(raw_output)

    def _get_session(self) -> ort.InferenceSession:
        if self._session is not None:
            return self._session

        model_path = settings.CLASSIFICATION_MODEL_PATH
        if model_path is None or not model_path.exists():
            raise RuntimeError(f"Classification model not found: {model_path}")

        self._session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name
        return self._session

    def _preprocess_image(self, image: Image.Image) -> np.ndarray:
        image = image.resize((224, 224), Image.BILINEAR)
        array = np.asarray(image, dtype=np.float32) / 255.0

        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        array = (array - mean) / std

        array = np.transpose(array, (2, 0, 1))[None, ...]
        return array.astype(np.float32)

    def _softmax(self, logits: np.ndarray) -> np.ndarray:
        logits = logits - np.max(logits)
        exp = np.exp(logits)
        return exp / np.sum(exp)


classification_service = ClassificationService()
