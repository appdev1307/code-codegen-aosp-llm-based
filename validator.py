def log(msg):
    print(f"[VALIDATOR] {msg}", flush=True)

# -----------------------------
# Individual checks
# -----------------------------

def check_vhal(vhal_code: str):
    issues = []
    if "while(true)" in vhal_code.replace(" ", ""):
        issues.append("VHAL contains blocking while(true) loop (VTS violation)")
    if "sleep(" in vhal_code:
        issues.append("VHAL uses sleep() in HAL thread (VTS violation)")
    return issues

def check_car_service(car_code: str):
    issues = []
    if "enforceCallingPermission" not in car_code:
        issues.append("CarService missing enforceCallingPermission (CTS violation)")
    return issues

def check_selinux(policy: str):
    issues = []
    if "allow * *:* *;" in policy:
        issues.append("SELinux policy is overly permissive (neverallow violation)")
    return issues

# -----------------------------
# Public API
# -----------------------------

def validate_all(vhal_code: str, car_code: str, selinux_policy: str):
    log("Running CTS/VTS-style validation...")

    all_issues = []

    all_issues += check_vhal(vhal_code)
    all_issues += check_car_service(car_code)
    all_issues += check_selinux(selinux_policy)

    if all_issues:
        log("❌ VALIDATION FAILED")
        for issue in all_issues:
            log(f"- {issue}")
    else:
        log("✅ VALIDATION PASSED (No CTS/VTS violations)")

    return all_issues
