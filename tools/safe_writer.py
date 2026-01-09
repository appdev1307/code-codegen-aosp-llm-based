import os


class SafeWriter:
    """
    Enforces:
    - Relative paths only
    - No absolute paths
    - No path traversal
    - All writes stay inside output_dir
    """

    def __init__(self, output_dir: str):
        self.output_dir = os.path.abspath(output_dir)

    def write(self, relative_path: str, content: str):
        if not relative_path:
            raise ValueError("Empty relative path")

        # Normalize
        relative_path = relative_path.lstrip("/")

        # Reject absolute paths
