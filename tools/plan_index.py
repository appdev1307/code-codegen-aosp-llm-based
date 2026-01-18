# tools/plan_index.py
from typing import Any, Dict, Optional

class PlanIndex:
    def __init__(self, plan: Dict[str, Any]):
        self.plan = plan or {}
        self.by_id = {p.get("id"): p for p in (self.plan.get("properties") or []) if p.get("id")}

    def callback_policy(self) -> str:
        return self.plan.get("callback_policy", "notify_on_change")

    def default_change_mode(self) -> str:
        return self.plan.get("default_change_mode", "ON_CHANGE")

    def prop_change_mode(self, prop_id: str) -> str:
        p = self.by_id.get(prop_id) or {}
        return p.get("change_mode") or self.default_change_mode()

    def prop_default(self, prop_id: str):
        p = self.by_id.get(prop_id) or {}
        return p.get("default", None)
