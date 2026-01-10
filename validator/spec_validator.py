from schemas.hal_spec import HalSpec

def validate_hal_spec(spec: HalSpec) -> None:
    if spec.domain not in ("HVAC", "ADAS", "MEDIA", "POWER"):
        raise ValueError(f"[SPEC ERROR] Unsupported domain: {spec.domain}")

    if not isinstance(spec.aosp_level, int) or spec.aosp_level < 12:
        raise ValueError(f"[SPEC ERROR] aosp_level must be >= 12, got {spec.aosp_level}")

    if not spec.properties:
        raise ValueError("[SPEC ERROR] No properties provided")

    for p in spec.properties:
        if not p.id:
            raise ValueError("[SPEC ERROR] Property id is empty")
        if p.type not in ("INT", "FLOAT", "BOOLEAN"):
            raise ValueError(f"[SPEC ERROR] Invalid property type for {p.id}: {p.type}")
        if p.access not in ("READ", "WRITE", "READ_WRITE"):
            raise ValueError(f"[SPEC ERROR] Invalid access for {p.id}: {p.access}")
        if p.areas is None:
            raise ValueError(f"[SPEC ERROR] Areas is None for {p.id}")
