# Simple CTS/VTS-style validator rules

def _read(path: str) -> str:
    with open(path, "r") as f:
        return f.read()


def validate_all(vhal_path: str, car_service_path: str, selinux_policy_path: str):
    issues = []

    vhal = _read(vhal_path)
    service = _read(car_service_path)
    sepolicy = _read(selinux_policy_path)

    # =====================
    # VHAL rules (VTS-like)
    # =====================
    if "while(true)" in vhal or "while (true)" in vhal:
        issues.append("VHAL: Blocking loop detected (VTS violation)")

    if "sleep(" in vhal:
        issues.append("VHAL: Sleep in HAL thread (VTS violation)")

    # =====================
    # CarService rules (CTS-like)
    # =====================
    if "enforceCallingPermission" not in service:
        issues.append("CarService: Missing permission enforcement (CTS violation)")

    # =====================
    # SELinux rules
    # =====================
    if "allow * *:* *;" in sepolicy:
        issues.append("SELinux: Over-permissive rule detected")

    # =====================
    # Result
    # =====================
    if not issues:
        print("[VALIDATOR] ✅ PASS: No CTS/VTS violations detected", flush=True)
    else:
        print("[VALIDATOR] ❌ FAILURES:", flush=True)
        for issue in issues:
            print(f"  - {issue}", flush=True)

    return issues
