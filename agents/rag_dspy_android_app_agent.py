"""agents/rag_dspy_android_app_agent.py
RAG + DSPy Android Automotive App Generation Agent.
"""

from __future__ import annotations
import time
import re
from pathlib import Path
from agents.rag_dspy_mixin import RAGDSPyMixin


class RAGDSPyAndroidAppAgent(RAGDSPyMixin):
    AGENT_TYPE = "android_app"
    DSPY_OUTPUT_FIELD = "kotlin_code"
    _BASE_PACKAGE = "com.vss.hal"

    def __init__(self, dspy_programs_dir: str = "dspy_opt/saved", rag_top_k: int = 3,
                 rag_db_path: str = "rag/chroma_db", output_dir: str = "android_app", output_root: str = ""):
        self._init_rag_dspy(dspy_programs_dir=dspy_programs_dir, rag_top_k=rag_top_k, rag_db_path=rag_db_path)
        self._output_dir = Path(output_root) / "android_app" if output_root else Path(output_dir)
        self._layout_module = self._load_layout_module(dspy_programs_dir)

    def _load_layout_module(self, programs_dir: str):
        try:
            from dspy_opt.hal_modules import get_module
            return get_module("android_layout", programs_dir=programs_dir, auto_load=True)
        except Exception as e:
            self._log(f"Layout module not available: {e}")
            return None

    def _write_app_scaffold(self) -> None:
        """Create basic app structure."""
        # Manifest
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

        # Icons
        self._create_placeholder_icons()

    def _create_placeholder_icons(self) -> None:
        """Create minimal placeholder launcher icons."""
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
            self._log("Created placeholder launcher icons")
        except Exception as e:
            self._log(f"Failed to create placeholder icons: {e}")

    def run(self, module_signal_map: dict, properties: list) -> None:
        t_start = time.time()
        self._write_app_scaffold()
        self._log(f"Generating Android app for {len(module_signal_map)} module(s)")

        for domain, signal_names in module_signal_map.items():
            if not signal_names:
                continue
            self._generate_module(domain, signal_names, properties)

        self._log(f"Android app generation completed in {time.time() - t_start:.1f}s")

    def _generate_module(self, domain: str, signal_names: list[str], all_properties: list) -> None:
        prop_ids = set(signal_names)
        module_props = [p for p in all_properties if getattr(p, "id", "") in prop_ids]
        prop_lines = "\n".join(f"- {getattr(p, 'id', '')} ({getattr(p, 'type', 'UNKNOWN')})" for p in module_props)

        aosp_context = self._retrieve_multi([
            f"CarPropertyManager {domain} Kotlin example",
            f"CarPropertyEventCallback Android Automotive",
        ])

        kt_content = self._generate(domain=domain, properties=prop_lines, aosp_context=aosp_context)
        self._write_kotlin(domain, kt_content)

        self._generate_layout(domain, prop_lines, len(module_props) or len(signal_names))

    def _generate_layout(self, domain: str, prop_lines: str, prop_count: int) -> None:
        if not self._layout_module:
            return

        LAYOUT_CHUNK_SIZE = 15
        layout_context = self._retrieve("Android layout XML ScrollView LinearLayout TextView Switch SeekBar")

        try:
            if prop_count <= LAYOUT_CHUNK_SIZE:
                result = self._layout_module(domain=domain, properties=prop_lines, aosp_context=layout_context)
                layout_content = getattr(result, "layout_xml", "") or ""
            else:
                self._log(f"Large domain {domain} ({prop_count} props) → chunking")
                all_props = prop_lines.splitlines()
                chunks = [all_props[i:i+LAYOUT_CHUNK_SIZE] for i in range(0, len(all_props), LAYOUT_CHUNK_SIZE)]
                inner_views = []
                for i, chunk in enumerate(chunks):
                    chunk_result = self._layout_module(
                        domain=f"{domain}_chunk{i+1}",
                        properties="\n".join(chunk),
                        aosp_context=layout_context
                    )
                    chunk_xml = getattr(chunk_result, "layout_xml", "") or ""
                    inner = re.sub(r'^<\?xml[^>]*>\s*', '', chunk_xml.strip(), flags=re.DOTALL)
                    inner = re.sub(r'^<[^>]+>\s*', '', inner, flags=re.DOTALL)
                    inner = re.sub(r'\s*</[^>]+>\s*$', '', inner, flags=re.DOTALL)
                    inner_views.append(inner.strip())

                layout_content = f'''<?xml version="1.0" encoding="utf-8"?>
<ScrollView xmlns:android="http://schemas.android.com/apk/res/android"
    android:layout_width="match_parent"
    android:layout_height="match_parent">
    <LinearLayout
        android:layout_width="match_parent"
        android:layout_height="wrap_content"
        android:orientation="vertical">
        {"\n".join(inner_views)}
    </LinearLayout>
</ScrollView>'''

            self._write_layout(domain, layout_content)
        except Exception as e:
            self._log(f"Layout failed for {domain}: {e}")

    def _write_kotlin(self, domain: str, content: str) -> None:
        class_name = f"{domain.capitalize()}Fragment"
        pkg_path = self._BASE_PACKAGE.replace(".", "/")
        out_path = self._output_dir / "src/main/java" / pkg_path / f"{class_name}.kt"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if content and content.strip():
            out_path.write_text(content, encoding="utf-8")
            self._log(f"Wrote {class_name}.kt")

    def _write_layout(self, domain: str, content: str) -> None:
        filename = f"fragment_{domain.lower()}.xml"
        out_path = self._output_dir / "src/main/res/layout" / filename
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if content and content.strip():
            out_path.write_text(content, encoding="utf-8")
            self._log(f"Wrote {filename}")