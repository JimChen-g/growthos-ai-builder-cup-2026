"""Load and validate the deterministic Phase 5 demo fixture."""

from pathlib import Path

from backend.models.demo import DemoScript
from backend.services.phase2_runtime import REPOSITORY_ROOT


def load_demo_script(path: Path) -> DemoScript:
    resolved = path if path.is_absolute() else REPOSITORY_ROOT / path
    return DemoScript.model_validate_json(resolved.read_text(encoding="utf-8"))
