import json
from pathlib import Path

import pytest

from backend.config import Settings
from backend.services.real_intelligence import build_real_intelligence_runtime


@pytest.mark.asyncio
async def test_all_connection_detail_copy_has_simplified_and_traditional_chinese() -> None:
    runtime = build_real_intelligence_runtime(Settings())
    center = await runtime.connections.center("translation-test")
    detail_copy = {
        text
        for connection in center.connections
        for text in (connection.setup_label, connection.note, *connection.setup_steps)
        if text
    }

    root = Path(__file__).parents[1] / "frontend" / "lib" / "i18n"
    for filename in ("server.zh-Hans.json", "server.zh-Hant.json"):
        translations = json.loads((root / filename).read_text())["exact"]
        missing = sorted(detail_copy - translations.keys())
        assert not missing, f"{filename} is missing connection copy: {missing}"
        untranslated = sorted(text for text in detail_copy if translations[text] == text)
        assert not untranslated, f"{filename} leaves connection copy in English: {untranslated}"
