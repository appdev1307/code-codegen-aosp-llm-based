from pathlib import Path
from typing import Union


class SafeWriter:
    def __init__(self, root_dir: Union[str, Path]):
        self.root = Path(root_dir).resolve()

    def write(self, rel_path: str, content: str) -> None:
        if not rel_path or not isinstance(rel_path, str):
            raise ValueError("SafeWriter.write(): rel_path is empty/invalid")

        rel_path = rel_path.replace("\\", "/").strip()
        if rel_path.startswith("/"):
            raise ValueError(f"SafeWriter.write(): absolute path rejected: {rel_path}")

        parts = [p for p in rel_path.split("/") if p]
        if any(p == ".." for p in parts):
            raise ValueError(f"SafeWriter.write(): path traversal rejected: {rel_path}")

        abs_path = (self.root / Path(*parts)).resolve()
        if self.root not in abs_path.parents and abs_path != self.root:
            raise ValueError(f"SafeWriter.write(): escaped root rejected: {rel_path}")

        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
