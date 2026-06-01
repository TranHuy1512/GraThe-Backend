import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from PIL import Image

from app.core.config import settings


class AIRestorer:
    def __init__(self) -> None:
        self._module: ModuleType | None = None

    def _load_module(self) -> ModuleType:
        if self._module is not None:
            return self._module

        ai_app_path = settings.AI_PROJECT_DIR / "app.py"
        if not ai_app_path.exists():
            raise RuntimeError(f"AI app file not found: {ai_app_path}")

        ai_project_path = str(settings.AI_PROJECT_DIR)
        if ai_project_path not in sys.path:
            sys.path.insert(0, ai_project_path)

        spec = importlib.util.spec_from_file_location("document_restoration_ai_app", ai_app_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot import AI app from: {ai_app_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self._module = module
        return module

    def restore_image(
        self,
        image: Image.Image,
        patch_size: int,
        batch_size: int,
        threshold: float,
        binarize_output: bool,
        overlap: bool,
    ) -> Image.Image:
        module = self._load_module()
        return module.enhance_document(
            image=image,
            patch_size=patch_size,
            batch_size=batch_size,
            threshold=threshold,
            binarize_output=binarize_output,
            overlap=overlap,
        )


ai_restorer = AIRestorer()
