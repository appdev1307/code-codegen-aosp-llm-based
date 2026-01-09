import os


class SafeWriter:
    """
    Enforces:
    - All paths are RELATIVE
    - No path traversal (..)
    - No absolute paths
    - All writes stay inside output_root
    """

    def __init__(self, output_root: str):
        self.output_root = os.path.abspath(output_root)
        os.makedirs(self.output_root, exist_ok=True)

    def write(self, rel_path: str, content: str):
        if rel_path.startswith("/") or ".." in rel_path:
            raise ValueError(f"[SAFE WRITER] Unsafe path detected: {rel_path}")

        abs_path = os.path.abspath(os.path.join(self.output_root, rel_path))

        if not abs_path.startswith(self.output_root):
            raise ValueError(f"[SAFE WRITER] Path escape detected: {abs_path}")

        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
