import re
from pathlib import Path


class ValidationResult:
    def __init__(self, ok: bool, errors=None, warnings=None):
        self.ok = ok
        self.errors = errors or []
        self.warnings = warnings or []

    def __str__(self):
        if self.ok:
            return "[VALIDATOR] ✅ VHAL validation PASSED"
        return (
            "[VALIDATOR] ❌ VHAL validation FAILED\n"
            + "\n".join(self.errors)
        )


class VHALValidator:
    """
    Static validator for generated VHAL C++ code (AAOS 13/14)
    """

    REQUIRED_INCLUDES = [
        "android/hardware/automotive/vehicle",
    ]

    REQUIRED_SYMBOLS = [
        "IVehicle",
        "VehiclePropValue",
    ]

    FORBIDDEN_PATTERNS = [
        r"using namespace std",
        r"#include <bits/",
    ]

    def validate(self, code: str) -> ValidationResult:
        errors = []
        warnings = []

        # 1. Strip markdown if any
        code = self._strip_markdown(code)

        # 2. Basic includes
        for inc in self.REQUIRED_INCLUDES:
            if inc not in code:
                errors.append(f"Missing required include: {inc}")

        # 3. Required symbols
        for sym in self.REQUIRED_SYMBOLS:
            if sym not in code:
                errors.append(f"Missing required VHAL symbol: {sym}")

        # 4. Forbidden patterns
        for pat in self.FORBIDDEN_PATTERNS:
            if re.search(pat, code):
                errors.append(f"Forbidden pattern detected: {pat}")

        # 5. Heuristic warnings
        if "TODO" in code:
            warnings.append("Found TODO in generated code")

        return ValidationResult(ok=len(errors) == 0, errors=errors, warnings=warnings)

    def _strip_markdown(self, text: str) -> str:
        """
        Remove ```cpp ... ``` blocks if present
        """
        if "```" not in text:
            return text

        blocks = re.findall(r"```(?:cpp)?\n(.*?)```", text, re.S)
        if blocks:
            return "\n".join(blocks)

        return text
