# FILE: agents/build_glue_agent.py
from pathlib import Path
from tools.safe_writer import SafeWriter
import json
import re


class BuildGlueAgent:
    """
    Generates proper AOSP build files (Android.bp) for automotive HAL modules.
    Includes validation, dynamic generation based on module properties, and proper AOSP conventions.
    """
    
    def __init__(self, output_root="output", module_plan=None, hal_spec=None):
        self.writer = SafeWriter(output_root)
        self.output_root = Path(output_root)
        self.module_plan = module_plan
        self.hal_spec = hal_spec
        
    def run(self):
        """Generate all required build files for AOSP."""
        print("[BUILD GLUE] Generating build files...")
        
        try:
            # Generate AIDL interface build file
            self._generate_aidl_bp()
            
            # Generate HAL implementation build file
            self._generate_impl_bp()
            
            # Generate VINTF manifest
            self._generate_vintf_manifest()
            
            # Generate init.rc file
            self._generate_init_rc()
            
            # Generate sepolicy file contexts (if needed)
            self._generate_file_contexts()
            
            print("[BUILD GLUE] Done")
            return True
            
        except Exception as e:
            print(f"[BUILD GLUE] Error: {e}")
            return False
    
    def _generate_aidl_bp(self):
        """Generate Android.bp for AIDL interface definition."""
        
        # Detect AIDL files in the output
        aidl_dir = self.output_root / "hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle"
        aidl_files = []
        
        if aidl_dir.exists():
            aidl_files = [f.name for f in aidl_dir.glob("*.aidl")]
        
        # Fallback to wildcard if no files found yet
        if not aidl_files:
            aidl_files = ["*.aidl"]
        
        aidl_bp = f"""// Generated Android.bp for Vehicle HAL AIDL interface
package {{
    default_applicable_licenses: ["Android-Apache-2.0"],
}}

aidl_interface {{
    name: "android.hardware.automotive.vehicle",
    vendor_available: true,
    srcs: ["android/hardware/automotive/vehicle/*.aidl"],
    stability: "vintf",
    backend: {{
        java: {{
            enabled: true,
            platform_apis: true,
        }},
        cpp: {{
            enabled: false,
        }},
        ndk: {{
            enabled: true,
        }},
    }},
    versions_with_info: [
        {{
            version: "3",
            imports: [],
        }},
    ],
    frozen: false,
}}
"""
        
        self.writer.write("hardware/interfaces/automotive/vehicle/aidl/Android.bp", aidl_bp)
        print("  [BUILD GLUE] ✓ AIDL Android.bp")
    
    def _generate_impl_bp(self):
        """Generate Android.bp for HAL service implementation."""
        
        # Collect all .cpp files in impl directory
        impl_dir = self.output_root / "hardware/interfaces/automotive/vehicle/impl"
        cpp_files = []
        
        if impl_dir.exists():
            cpp_files = [f.name for f in impl_dir.glob("*.cpp")]
        
        # Fallback to known files
        if not cpp_files:
            cpp_files = ["VehicleHalService.cpp"]
        
        # Determine modules from module_plan if available
        modules = []
        if self.module_plan and Path(self.module_plan).exists():
            with open(self.module_plan, 'r') as f:
                plan = json.load(f)
                modules = [m['name'] for m in plan.get('modules', [])]
        
        # Add module-specific implementation files
        for module in modules:
            module_impl = f"{module}Impl.cpp"
            if module_impl not in cpp_files:
                cpp_files.append(module_impl)
        
        # Format source files for Android.bp
        srcs_formatted = ",\n        ".join([f'"{f}"' for f in sorted(cpp_files)])
        
        impl_bp = f"""// Generated Android.bp for Vehicle HAL service
package {{
    default_applicable_licenses: ["Android-Apache-2.0"],
}}

cc_binary {{
    name: "android.hardware.automotive.vehicle-service",
    relative_install_path: "hw",
    init_rc: ["android.hardware.automotive.vehicle-service.rc"],
    vintf_fragments: ["manifest.xml"],
    vendor: true,
    srcs: [
        {srcs_formatted},
    ],
    shared_libs: [
        "libbase",
        "libbinder_ndk",
        "liblog",
        "libutils",
        "libjsoncpp",
    ],
    static_libs: [
        "android.hardware.automotive.vehicle-V3-ndk",
    ],
    cflags: [
        "-Wall",
        "-Wextra",
        "-Werror",
        "-DLOG_TAG=\\"VehicleHAL\\"",
    ],
}}
"""
        
        self.writer.write("hardware/interfaces/automotive/vehicle/impl/Android.bp", impl_bp)
        print("  [BUILD GLUE] ✓ Implementation Android.bp")
    
    def _generate_vintf_manifest(self):
        """Generate VINTF manifest fragment for HAL registration."""
        
        manifest = """<!-- Generated VINTF manifest for Vehicle HAL -->
<manifest version="1.0" type="device">
    <hal format="aidl">
        <name>android.hardware.automotive.vehicle</name>
        <version>3</version>
        <fqname>IVehicle/default</fqname>
    </hal>
</manifest>
"""
        
        self.writer.write("hardware/interfaces/automotive/vehicle/impl/manifest.xml", manifest)
        print("  [BUILD GLUE] ✓ VINTF manifest.xml")
    
    def _generate_init_rc(self):
        """Generate init.rc for service startup."""
        
        init_rc = """# Generated init.rc for Vehicle HAL service
service vendor.vehicle-hal /vendor/bin/hw/android.hardware.automotive.vehicle-service
    class hal
    user vehicle_network
    group system inet
    capabilities BLOCK_SUSPEND NET_BIND_SERVICE
    disabled

on property:vold.decrypt=trigger_restart_framework
    start vendor.vehicle-hal

on property:sys.boot_completed=1
    start vendor.vehicle-hal
"""
        
        self.writer.write(
            "hardware/interfaces/automotive/vehicle/impl/android.hardware.automotive.vehicle-service.rc",
            init_rc
        )
        print("  [BUILD GLUE] ✓ init.rc")
    
    def _generate_file_contexts(self):
        """Generate SELinux file_contexts if needed."""
        
        file_contexts = """# Generated file_contexts for Vehicle HAL
/vendor/bin/hw/android\\.hardware\\.automotive\\.vehicle-service    u:object_r:hal_vehicle_default_exec:s0
"""
        
        self.writer.write(
            "sepolicy/private/file_contexts",
            file_contexts
        )
        print("  [BUILD GLUE] ✓ file_contexts")
    
    def validate(self):
        """
        Validate that generated build files are correct.
        Returns: (bool, list of errors)
        """
        errors = []
        
        # Check AIDL Android.bp
        aidl_bp_path = self.output_root / "hardware/interfaces/automotive/vehicle/aidl/Android.bp"
        if not aidl_bp_path.exists():
            errors.append("Missing AIDL Android.bp")
        else:
            content = aidl_bp_path.read_text()
            if "aidl_interface" not in content:
                errors.append("AIDL Android.bp missing aidl_interface module")
            if "name:" not in content:
                errors.append("AIDL Android.bp missing name field")
        
        # Check Implementation Android.bp
        impl_bp_path = self.output_root / "hardware/interfaces/automotive/vehicle/impl/Android.bp"
        if not impl_bp_path.exists():
            errors.append("Missing Implementation Android.bp")
        else:
            content = impl_bp_path.read_text()
            if "cc_binary" not in content:
                errors.append("Implementation Android.bp missing cc_binary module")
            if "init_rc:" not in content:
                errors.append("Implementation Android.bp missing init_rc")
            if "vintf_fragments:" not in content:
                errors.append("Implementation Android.bp missing vintf_fragments")
        
        # Check VINTF manifest
        manifest_path = self.output_root / "hardware/interfaces/automotive/vehicle/impl/manifest.xml"
        if not manifest_path.exists():
            errors.append("Missing VINTF manifest.xml")
        
        # Check init.rc
        init_rc_path = self.output_root / "hardware/interfaces/automotive/vehicle/impl/android.hardware.automotive.vehicle-service.rc"
        if not init_rc_path.exists():
            errors.append("Missing init.rc file")
        
        return len(errors) == 0, errors


class ImprovedBuildGlueAgent(BuildGlueAgent):
    """
    Extended BuildGlueAgent with LLM-powered generation capabilities.
    Use this if you want to generate build files using LLM instead of templates.
    """
    
    def __init__(self, output_root="output", module_plan=None, hal_spec=None, llm_client=None):
        super().__init__(output_root, module_plan, hal_spec)
        self.llm_client = llm_client
    
    def _generate_with_llm(self, component_type, context):
        """
        Generate build file using LLM with retry logic.
        
        Args:
            component_type: 'aidl_bp', 'impl_bp', 'vintf', 'init_rc'
            context: dict with relevant information
        
        Returns:
            str: Generated content
        """
        if not self.llm_client:
            # Fallback to template
            return self._generate_from_template(component_type, context)
        
        template = self._get_template(component_type)
        prompt = self._create_prompt(component_type, context, template)
        
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                result = self.llm_client.generate(prompt)
                cleaned = self._post_process(result)
                
                if self._validate_content(component_type, cleaned):
                    return cleaned
                
                # Add error feedback for retry
                errors = self._get_validation_errors(component_type, cleaned)
                prompt += f"\n\nATTEMPT {attempt + 1} FAILED. FIX THESE ERRORS:\n{errors}"
                
            except Exception as e:
                print(f"  [BUILD GLUE] LLM attempt {attempt + 1} failed: {e}")
        
        # Fallback to template after all retries
        print(f"  [BUILD GLUE] Falling back to template for {component_type}")
        return self._generate_from_template(component_type, context)
    
    def _create_prompt(self, component_type, context, template):
        """Create detailed prompt for LLM."""
        
        base_prompt = f"""Generate a valid Android.bp or configuration file for AOSP automotive HAL.

COMPONENT TYPE: {component_type}
CONTEXT: {json.dumps(context, indent=2)}

TEMPLATE TO FOLLOW:
{template}

REQUIREMENTS:
1. Use ONLY valid Soong syntax (Android.bp) or XML/RC syntax
2. Include all required fields
3. Properly format lists and dependencies
4. NO markdown code blocks, NO explanations
5. Return ONLY the file content

OUTPUT:"""
        
        return base_prompt
    
    def _post_process(self, llm_output):
        """Clean up LLM output."""
        # Remove markdown code blocks
        cleaned = re.sub(r'```[a-z]*\n?', '', llm_output)
        cleaned = re.sub(r'```', '', cleaned)
        
        # Remove common LLM preambles
        lines = cleaned.split('\n')
        if lines and ('here' in lines[0].lower() or 'generate' in lines[0].lower()):
            lines = lines[1:]
        
        return '\n'.join(lines).strip()
    
    def _validate_content(self, component_type, content):
        """Validate generated content."""
        if component_type == 'aidl_bp':
            return 'aidl_interface' in content and 'name:' in content
        elif component_type == 'impl_bp':
            return 'cc_binary' in content and 'srcs:' in content
        elif component_type == 'vintf':
            return '<manifest' in content and '<hal' in content
        elif component_type == 'init_rc':
            return 'service' in content
        return False
    
    def _get_validation_errors(self, component_type, content):
        """Get specific validation errors."""
        errors = []
        
        if component_type in ['aidl_bp', 'impl_bp']:
            if content.count('{') != content.count('}'):
                errors.append("Mismatched braces")
            if '```' in content:
                errors.append("Contains markdown artifacts")
        
        return '\n'.join(errors) if errors else "Invalid format"
    
    def _get_template(self, component_type):
        """Get template for component type."""
        templates = {
            'aidl_bp': """aidl_interface {
    name: "android.hardware.automotive.vehicle",
    srcs: ["android/hardware/automotive/vehicle/*.aidl"],
    stability: "vintf",
    backend: {
        ndk: { enabled: true },
        java: { enabled: true, platform_apis: true },
    },
}""",
            'impl_bp': """cc_binary {
    name: "android.hardware.automotive.vehicle-service",
    srcs: ["VehicleHalService.cpp"],
    shared_libs: ["libbase", "libbinder_ndk"],
    static_libs: ["android.hardware.automotive.vehicle-V3-ndk"],
}"""
        }
        return templates.get(component_type, "")
    
    def _generate_from_template(self, component_type, context):
        """Fallback template-based generation."""
        # Delegate to parent class template methods
        if component_type == 'aidl_bp':
            return self._generate_aidl_bp_template(context)
        elif component_type == 'impl_bp':
            return self._generate_impl_bp_template(context)
        return ""


# Convenience function for quick usage
def generate_build_files(output_root="output", module_plan=None, hal_spec=None):
    """
    Quick helper to generate all build files.
    
    Usage:
        from agents.build_glue_agent import generate_build_files
        success = generate_build_files("output", "output/MODULE_PLAN.json")
    """
    agent = BuildGlueAgent(output_root, module_plan, hal_spec)
    success = agent.run()
    
    if success:
        is_valid, errors = agent.validate()
        if not is_valid:
            print(f"[BUILD GLUE] Validation warnings: {errors}")
        else:
            print("[BUILD GLUE] All build files validated successfully ✓")
    
    return success