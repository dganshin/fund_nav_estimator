from __future__ import annotations


def normalize_asset_code(asset_code: str) -> str:
    code = asset_code.strip().upper()
    if code.startswith(("SH", "SZ", "BJ")) and len(code) > 2 and "." not in code:
        market = code[:2]
        digits = code[2:]
        return f"{digits}.{market}"
    if "." in code:
        digits, market = code.split(".", 1)
        return f"{digits}.{market.upper()}"
    if len(code) == 5 and code.isdigit():
        return f"{code}.HK"
    if len(code) == 6:
        if code.startswith("1"):
            return f"{code}.SZ"
        if code.startswith("5"):
            return f"{code}.SH"
        if code.startswith(("6", "9")):
            return f"{code}.SH"
        if code.startswith(("0", "2", "3")):
            return f"{code}.SZ"
        if code.startswith(("4", "8")):
            return f"{code}.BJ"
    return code


def to_plain_symbol(asset_code: str) -> str:
    normalized = normalize_asset_code(asset_code)
    if "." in normalized:
        return normalized.split(".", 1)[0]
    return normalized


def to_prefixed_symbol(asset_code: str) -> str:
    normalized = normalize_asset_code(asset_code)
    if "." not in normalized:
        raise ValueError(f"Unsupported asset code: {asset_code}")
    digits, market = normalized.split(".", 1)
    return f"{market.lower()}{digits}"
