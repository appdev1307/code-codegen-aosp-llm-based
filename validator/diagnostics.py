from enum import Enum
from typing import List, Dict
import json


class Severity(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class Artifact(Enum):
    AIDL = "AIDL"
    VHAL_SERVICE = "VHAL_SERVICE"
    CONTRACT = "CONTRACT"
    CARSERVICE = "CARSERVICE"
    SEPOLICY = "SEPOLICY"


def make_issue(
    code: str,
    message: str,
    artifact: Artifact,
    severity: Severity = Severity.ERROR,
) -> Dict:
    return {
        "code": code,
        "severity": severity.value,
        "artifact": artifact.value,
        "message": message,
    }


def emit_json_report(issues: List[Dict], path="output/validation_report.json"):
    data = {
        "status": "PASS" if not issues else "FAIL",
        "issue_count": len(issues),
        "issues": issues,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
