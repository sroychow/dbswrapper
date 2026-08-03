from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._+-]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_component(value: str) -> str:
    value = value.strip().strip("/")
    cleaned = _SAFE_COMPONENT.sub("_", value)
    return cleaned[:240] or "unknown"


def dataset_output_dir(root: Path, dataset: str) -> Path:
    parts = [part for part in dataset.split("/") if part]
    if len(parts) == 3:
        primary, processed, tier = parts
        return root / "datasets" / safe_component(primary) / safe_component(processed) / safe_component(tier)
    return root / "datasets" / safe_component(dataset)


def write_json(path: Path, payload: Any, *, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(
            payload,
            stream,
            indent=2 if pretty else None,
            sort_keys=pretty,
            ensure_ascii=False,
        )
        stream.write("\n")
    temporary.replace(path)
