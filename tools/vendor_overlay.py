from pathlib import Path


def vendor_overlay_root(vendor: str, domain: str) -> Path:
    root = Path("out/vendor") / vendor / "automotive/vehicle" / domain.lower()
    root.mkdir(parents=True, exist_ok=True)
    return root
