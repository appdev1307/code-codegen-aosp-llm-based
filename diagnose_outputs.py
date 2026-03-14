#!/usr/bin/env python3
"""
diagnose_outputs.py
───────────────────
Discovers the actual file layout in all three output directories
so we can fix the rescore script to match.

Run from project root:
    python diagnose_outputs.py
"""

from pathlib import Path
import os

PROJECT_ROOT = Path(".")

DIRS_TO_CHECK = [
    "output",
    "output_adaptive",
    "output_rag_dspy",
    # Common alternatives
    "outputs",
    "output/results",
    "results",
    "generated",
]

def scan_dir(d: Path, max_depth=4, prefix=""):
    """Recursively list directory contents."""
    if not d.exists():
        return
    items = sorted(d.iterdir())
    for item in items[:50]:  # cap at 50 per dir
        rel = item.relative_to(d.parents[0] if prefix else d)
        if item.is_dir():
            print(f"  {prefix}{item.name}/")
            if max_depth > 0:
                scan_dir(item, max_depth - 1, prefix + "  ")
        else:
            size = item.stat().st_size
            ext = item.suffix
            print(f"  {prefix}{item.name}  ({size:,} bytes)")


def main():
    print("=" * 60)
    print("OUTPUT DIRECTORY DIAGNOSTIC")
    print("=" * 60)
    print(f"Working directory: {os.getcwd()}")
    print()

    # 1. Check which dirs exist
    print("── Checking known output directories ──")
    for dirname in DIRS_TO_CHECK:
        d = PROJECT_ROOT / dirname
        if d.exists():
            file_count = sum(1 for _ in d.rglob("*") if _.is_file())
            print(f"  ✓ {dirname}/  exists  ({file_count} files)")
        else:
            print(f"  ✗ {dirname}/  not found")

    print()

    # 2. Detailed tree for each output dir
    for dirname in ["output", "output_adaptive", "output_rag_dspy"]:
        d = PROJECT_ROOT / dirname
        print(f"── {dirname}/ ──")
        if not d.exists():
            print(f"  NOT FOUND")
        else:
            # Show all file extensions
            extensions = {}
            all_files = list(d.rglob("*"))
            files_only = [f for f in all_files if f.is_file()]
            for f in files_only:
                ext = f.suffix or "(no ext)"
                extensions[ext] = extensions.get(ext, 0) + 1

            print(f"  Total files: {len(files_only)}")
            print(f"  Extensions: {dict(sorted(extensions.items()))}")
            print(f"  Tree:")
            scan_dir(d, max_depth=3)
        print()

    # 3. Also check for loose files in project root that might be outputs
    print("── Scoreable files in project root ──")
    root_extensions = [".aidl", ".cpp", ".te", ".bp", ".md", ".kt", ".py"]
    for f in sorted(PROJECT_ROOT.iterdir()):
        if f.is_file() and f.suffix in root_extensions:
            print(f"  {f.name}  ({f.stat().st_size:,} bytes)")

    # 4. Check experiments/results
    print()
    print("── experiments/results/ ──")
    rd = PROJECT_ROOT / "experiments" / "results"
    if rd.exists():
        scan_dir(rd, max_depth=1)
    else:
        print("  NOT FOUND")

    print()
    print("Done. Copy this full output and share it so we can fix the rescore paths.")


if __name__ == "__main__":
    main()
