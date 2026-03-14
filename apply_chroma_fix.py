#!/usr/bin/env python3
"""
apply_chroma_fix.py
───────────────────
Patches multi_main_rag_dspy.py to add the ChromaDB singleton fix
at the top of the file. Run once, then re-run the C3 pipeline.

Usage:
    python apply_chroma_fix.py
"""

from pathlib import Path
import shutil

MAIN_FILE = Path("multi_main_rag_dspy.py")
PATCH_IMPORT = """# ── ChromaDB singleton fix (prevents "instance already exists" error) ──
import fix_chroma_singleton
fix_chroma_singleton.patch_chromadb()
# ── End fix ──────────────────────────────────────────────────────────────
"""

def main():
    if not MAIN_FILE.exists():
        print(f"✗ {MAIN_FILE} not found — run from project root")
        return

    content = MAIN_FILE.read_text()

    if "fix_chroma_singleton" in content:
        print("✓ Patch already applied")
        return

    # Backup
    backup = MAIN_FILE.with_suffix(".py.bak")
    shutil.copy2(MAIN_FILE, backup)
    print(f"  Backup → {backup}")

    # Find insertion point: after initial imports, before agent imports
    lines = content.splitlines(keepends=True)
    insert_idx = 0

    # Strategy: insert after the last `import` or `from` line that comes
    # before any agent-related import or function definition
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")) and "agent" not in stripped.lower():
            insert_idx = i + 1
        elif stripped.startswith(("def ", "class ", "if __name__")):
            break

    # Insert patch
    lines.insert(insert_idx, "\n" + PATCH_IMPORT + "\n")
    MAIN_FILE.write_text("".join(lines))
    print(f"✓ Patched {MAIN_FILE} at line {insert_idx + 1}")
    print(f"  Now re-run: python {MAIN_FILE}")


if __name__ == "__main__":
    main()
