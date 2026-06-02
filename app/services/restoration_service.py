from pathlib import Path
from shutil import copyfile
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from gradio_client import Client, handle_file

from app.core.config import settings
from app.schemas.restoration import RestorationResponse, RestoredFile
from app.services.document_loader import is_supported_image
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

        if not is_supported_image(upload_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported file type. Please upload a degraded image file.",
            )

        request_output_dir = settings.RESTORED_DIR / request_id
        request_output_dir.mkdir(parents=True, exist_ok=True)

        restored_path = self._restore_with_remote_model(
            image_path=upload_path,
            output_dir=request_output_dir,
            patch_size=patch_size,
            batch_size=batch_size,
            threshold=threshold,
            binarize_output=binarize_output,
            overlap=overlap,
        )

        output_filename = f"page-001{restored_path.suffix or '.png'}"
        output_path = request_output_dir / output_filename
        if restored_path.resolve() != output_path.resolve():
            copyfile(restored_path, output_path)

        outputs = [
            RestoredFile(
                page=1,
                filename=output_filename,
                url=f"{settings.RESTORED_URL_PREFIX}/{request_id}/{output_filename}",
            )
        ]

        return RestorationResponse(
            request_id=request_id,
            input_filename=file.filename or upload_path.name,
            input_type="image",
            total_pages=len(outputs),
            outputs=outputs,
        )

    def _restore_with_remote_model(
        self,
        image_path: Path,
        output_dir: Path,
        patch_size: int,
        batch_size: int,
        threshold: float,
        binarize_output: bool,
        overlap: bool,
    ) -> Path:
        try:
            # client = Client(settings.AI_RESTORATION_SPACE, download_files=output_dir)
            client = Client(settings.AI_RESTORATION_SPACE)
            result = client.predict(
                image=handle_file(str(image_path)),
                patch_size=patch_size,
                batch_size=batch_size,
                threshold=threshold,
                binarize_output=binarize_output,
                overlap=overlap,
                api_name=settings.AI_RESTORATION_API_NAME,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="AI restoration service failed to process the image.",
            ) from exc

        restored_path = self._extract_restored_path(result)
        if restored_path is None or not restored_path.exists():
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="AI restoration service did not return a restored image file.",
            )
        return restored_path

    def _extract_restored_path(self, result: object) -> Path | None:
        if isinstance(result, (str, Path)):
            return Path(result)
        if isinstance(result, dict):
            path = result.get("path") or result.get("name")
            return Path(path) if isinstance(path, str) else None
        if isinstance(result, (list, tuple)):
            for item in result:
                path = self._extract_restored_path(item)
                if path is not None:
                    return path
        return None

    async def _save_upload(self, file: UploadFile, request_id: str) -> Path:
        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename."
            )

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

    def _validate_options(
        self, patch_size: int, batch_size: int, threshold: float
    ) -> None:
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
