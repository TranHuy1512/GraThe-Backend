from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings
from app.schemas.restoration import RestorationResponse, RestoredFile
from app.services.ai_restorer import ai_restorer
from app.services.document_loader import load_document_pages
from app.utils.files import safe_filename


class RestorationService:
    async def restore_upload(
        self,
        file: UploadFile,
        patch_size: int,
        batch_size: int,
        threshold: float,
        binarize_output: bool,
        overlap: bool,
    ) -> RestorationResponse:
        self._validate_options(patch_size, batch_size, threshold)

        request_id = uuid4().hex
        upload_path = await self._save_upload(file, request_id)

        try:
            input_type, pages = load_document_pages(upload_path)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        request_output_dir = settings.RESTORED_DIR / request_id
        request_output_dir.mkdir(parents=True, exist_ok=True)

        outputs: list[RestoredFile] = []
        for index, image in enumerate(pages, start=1):
            restored = ai_restorer.restore_image(
                image=image,
                patch_size=patch_size,
                batch_size=batch_size,
                threshold=threshold,
                binarize_output=binarize_output,
                overlap=overlap,
            )
            output_filename = f"page-{index:03d}.png"
            output_path = request_output_dir / output_filename
            restored.save(output_path, format="PNG")
            outputs.append(
                RestoredFile(
                    page=index,
                    filename=output_filename,
                    url=f"{settings.RESTORED_URL_PREFIX}/{request_id}/{output_filename}",
                )
            )

        return RestorationResponse(
            request_id=request_id,
            input_filename=file.filename or upload_path.name,
            input_type=input_type,
            total_pages=len(outputs),
            outputs=outputs,
        )

    async def _save_upload(self, file: UploadFile, request_id: str) -> Path:
        if not file.filename:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename.")

        content = await file.read()
        max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File is larger than {settings.MAX_UPLOAD_MB} MB.",
            )

        filename = safe_filename(file.filename)
        upload_path = settings.UPLOAD_DIR / f"{request_id}-{filename}"
        upload_path.write_bytes(content)
        return upload_path

    def _validate_options(self, patch_size: int, batch_size: int, threshold: float) -> None:
        if patch_size not in {256, 384, 512, 768}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="patch_size must be one of: 256, 384, 512, 768.",
            )
        if batch_size < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="batch_size must be greater than or equal to 1.",
            )
        if not 0 <= threshold <= 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="threshold must be between 0 and 1.",
            )


restoration_service = RestorationService()
