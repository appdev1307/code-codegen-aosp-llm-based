"""agents/rag_dspy_android_app_agent.py
RAG + DSPy Android Automotive App Generation Agent.
Generates Kotlin Fragments + XML layouts for each HAL module.
"""

from __future__ import annotations
import time
from pathlib import Path
from agents.rag_dspy_mixin import RAGDSPyMixin


class RAGDSPyAndroidAppAgent(RAGDSPyMixin):
    """
    Generates Android Automotive app using RAG + DSPy.
    Compatible with LLMAndroidAppAgentAdaptive interface.
    """
    AGENT_TYPE = "android_app"
    DSPY_OUTPUT_FIELD = "kotlin_code"
    _BASE_PACKAGE = "com.vss.hal"

    def __init__(
        self,
        dspy_programs_dir: str = "dspy_opt/saved",
        rag_top_k: int = 3,
        rag_db_path: str = "rag/chroma_db",
        output_dir: str = "android_app",
        output_root: str = "",
    ):
        self._init_rag_dspy(
            dspy_programs_dir=dspy_programs_dir,
            rag_top_k=rag_top_k,
            rag_db_path=rag_db_path,
        )

        self._output_dir = Path(output_root) / "android_app" if output_root else Path(output_dir)
        self._layout_module = self._load_layout_module(dspy_programs_dir)

    def _load_layout_module(self, programs_dir: str):
        """Load Android layout DSPy module."""
        try:
            from dspy_opt.hal_modules import get_module
            return get_module("android_layout", programs_dir=programs_dir, auto_load=True)
        except Exception as e:
            self._log(f"Layout module not available: {e}")
            return None

    def _write_app_scaffold(self) -> None:
        """Create basic app structure: Manifest, strings, and placeholder icons."""
        # AndroidManifest.xml
        manifest_path = self._output_dir / "src/main/AndroidManifest.xml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if not manifest_path.exists():
            manifest_path.write_text("""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.vss.vehicleapp">

    <uses-permission android:name="android.car.permission.CAR_ENERGY" />
    <uses-permission android:name="android.car.permission.CAR_SPEED" />

    <application
        android:allowBackup="true"
        android:label="@string/app_name"
        android:icon="@mipmap/ic_launcher"
        android:roundIcon="@mipmap/ic_launcher"
        android:supportsRtl="true"
        android:theme="@android:style/Theme.Material.Light">

        <activity android:name="android.app.Activity"
            android:exported="true"
            android:label="@string/app_name">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
""")

        # strings.xml
        strings_path = self._output_dir / "src/main/res/values/strings.xml"
        strings_path.parent.mkdir(parents=True, exist_ok=True)
        if not strings_path.exists():
            strings_path.write_text("""<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">VSS Dashboard</string>
</resources>
""")

        # Create placeholder icons
        self._create_placeholder_icons()

    def _create_placeholder_icons(self) -> None:
        """Create minimal 1x1 placeholder icons."""
        try:
            import struct, zlib
            def _make_png():
                raw = b"\x00\x42\x85\xF4"  # Blue color
                compressed = zlib.compress(raw)
                def chunk(name, data):
                    c = name + data
                    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
                png = b"\x89PNG\r\n\x1a\n"
                png += chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
                png += chunk(b"IDAT", compressed)
                png += chunk(b"IEND", b"")
                return png

            png_data = _make_png()
            for density in ["mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi"]:
                icon_path = self._output_dir / f"src/main/res/mipmap-{density}/ic_launcher.png"
                icon_path.parent.mkdir(parents=True, exist_ok=True)
                if not icon_path.exists():
                    icon_path.write_bytes(png_data)
        except Exception as e:
            self._log(f"Failed to create placeholder icons: {e}")

    def run(self, module_signal_map: dict, properties: list) -> None:
        """Main entry point."""
        t_start = time.time()
        self._write_app_scaffold()

        self._log(f"Generating Android app for {len(module_signal_map)} module(s)")

        for domain, signal_names in module_signal_map.items():
            if not signal_names:
                self._log(f"Skipping {domain} — empty signals")
                continue
            self._generate_module(domain, signal_names, properties)

        elapsed = time.time() - t_start
        self._log(f"Android app generation completed in {elapsed:.1f}s")

    def _generate_module(self, domain: str, signal_names: list[str], all_properties: list) -> None:
        """Generate Fragment + Layout for one domain."""
        prop_ids = set(signal_names)
        module_props = [p for p in all_properties if getattr(p, "id", "") in prop_ids]

        prop_lines = "\n".join(
            f"- {getattr(p, 'id', '')} ({getattr(p, 'type', 'UNKNOWN')})"
            for p in module_props
        ) or "\n".join(f"- {name}" for name in signal_names[:15])

        aosp_context = self._retrieve_multi([
            f"CarPropertyManager {domain} Kotlin example",
            f"CarPropertyEventCallback Android Automotive",
        ])

        kt_content = self._generate(
            domain=domain,
            properties=prop_lines,
            aosp_context=aosp_context,
        )
        self._write_kotlin(domain, kt_content)
        self._generate_layout(domain, prop_lines)

    def _generate_layout(self, domain: str, prop_lines: str) -> None:
        """Generate XML layout with chunking support."""
        if not self._layout_module:
            self._log(f"Skipping layout for {domain} — layout module not loaded")
            return

        layout_context = self._retrieve("Android layout XML ScrollView LinearLayout TextView Switch")
        try:
            result = self._layout_module(
                domain=domain,
                properties=prop_lines,
                aosp_context=layout_context,
            )
            layout_content = getattr(result, "layout_xml", "") or ""
            self._write_layout(domain, layout_content)
        except Exception as e:
            self._log(f"Layout generation failed for {domain}: {e}")

    def _write_kotlin(self, domain: str, content: str) -> None:
        class_name = f"{domain.capitalize()}Fragment"
        pkg_path = self._BASE_PACKAGE.replace(".", "/")
        out_path = self._output_dir / "src/main/java" / pkg_path / f"{class_name}.kt"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if content and content.strip():
            out_path.write_text(content, encoding="utf-8")
            self._log(f"Wrote {class_name}.kt")
        else:
            self._log(f"Empty Kotlin output for {domain}")

    def _write_layout(self, domain: str, content: str) -> None:
        filename = f"fragment_{domain.lower()}.xml"
        out_path = self._output_dir / "src/main/res/layout" / filename
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if content and content.strip():
            out_path.write_text(content, encoding="utf-8")
            self._log(f"Wrote {filename}")
        else:
            self._log(f"Empty layout output for {domain}")