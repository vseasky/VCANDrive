"""Known firmware capability corrections, independent of CAN/USB backends."""
HW_PLATFORM_SHIFT = 16
HW_PLATFORM_MASK = 0xFF
HW_PLATFORM_HC32 = 32
HC32_NOMINAL_TSEG1_MAX = 64
HC32_DATA_TSEG1_MAX = 16
HC32_TSEG2_MAX = 8
HC32_SJW_MAX = 8
HC32_BRP_MAX = 256


def effective_capabilities(capabilities: dict, device_info: dict | None) -> dict:
    """Intersect HC32 advertised timing ranges with REF BSP constraints."""
    result = dict(capabilities)
    raw_hw = device_info.get("raw_hw_version") if isinstance(device_info, dict) else None
    if not isinstance(raw_hw, int) or (raw_hw >> HW_PLATFORM_SHIFT) & HW_PLATFORM_MASK != HW_PLATFORM_HC32:
        return result
    for name, tseg1_max in (("nominal", HC32_NOMINAL_TSEG1_MAX), ("data", HC32_DATA_TSEG1_MAX)):
        limits = result.get(name)
        if limits is None:
            continue
        result["reported_" + name] = limits
        t1min, t1max, t2min, t2max, sjw, brpmin, brpmax, inc = limits
        result[name] = (t1min, min(t1max, tseg1_max), t2min,
                        min(t2max, HC32_TSEG2_MAX), min(sjw, HC32_SJW_MAX),
                        brpmin, min(brpmax, HC32_BRP_MAX), inc)
    result["timing_limits_source"] = "HC32 BSP compatibility"
    return result
