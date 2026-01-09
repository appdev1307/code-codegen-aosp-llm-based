from pathlib import Path


def create_base_layout(root: Path):
    paths = [
        "core/aidl",
        "core/service",
        "core/router",
        "domains",
        "vendor",
        "config",
    ]
    for p in paths:
        (root / p).mkdir(parents=True, exist_ok=True)
