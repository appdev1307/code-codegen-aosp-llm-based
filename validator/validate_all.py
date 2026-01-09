from validator.aidl_validator import validate_aidl
from validator.vhal_service_validator import validate_vhal_service
from validator.aidl_service_contract_validator import (
    validate_aidl_service_contract,
)
from validator.diagnostics import (
    make_issue,
    Artifact,
    Severity,
)


def validate_all(aidl, vhal_service, car_service, sepolicy):
    issues = []

    # ============================================================
    # AIDL
    # ============================================================
    if not aidl:
        issues.append(
            make_issue(
                code="AIDL-000",
                artifact=Artifact.AIDL,
                severity=Severity.ERROR,
                message="Missing AIDL definition",
            )
        )
    else:
        issues += validate_aidl(aidl)

    # ============================================================
    # VHAL Service
    # ============================================================
    if not vhal_service:
        issues.append(
            make_issue(
                code="VHAL-000",
                artifact=Artifact.VHAL_SERVICE,
                severity=Severity.ERROR,
                message="Missing VHAL service implementation",
            )
        )
    else:
        issues += validate_vhal_service(vhal_service)

    # ============================================================
    # AIDL ↔ Service Contract
    # ============================================================
    if aidl and vhal_service:
        issues += validate_aidl_service_contract(aidl, vhal_service)

    # ============================================================
    # CarService
    # ============================================================
    if not car_service:
        issues.append(
            make_issue(
                code="CARSERVICE-000",
                artifact=Artifact.CARSERVICE,
                severity=Severity.ERROR,
                message="Missing CarService implementation",
            )
        )
    else:
        if "CarPropertyManager" not in car_service:
            issues.append(
                make_issue(
                    code="CARSERVICE-001",
                    artifact=Artifact.CARSERVICE,
                    severity=Severity.ERROR,
                    message="Missing CarPropertyManager usage",
                )
            )

    # ============================================================
    # SELinux
    # ============================================================
    if not sepolicy:
        issues.append(
            make_issue(
                code="SELINUX-000",
                artifact=Artifact.SEPOLICY,
                severity=Severity.ERROR,
                message="Missing SEPolicy file",
            )
        )
    else:
        if "type car_hvac_service" not in sepolicy:
            issues.append(
                make_issue(
                    code="SELINUX-001",
                    artifact=Artifact.SEPOLICY,
                    severity=Severity.ERROR,
                    message="Missing car_hvac_service type",
                )
            )

    return issues
