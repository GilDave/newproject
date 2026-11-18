import re

_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_WHOLE = re.compile(r"(?:\d+[A-Za-z]\s*)+")
_TOKEN = re.compile(r"(\d+)([A-Za-z])")


def parse_duration(text):
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    s = text.strip()
    if not s or not _WHOLE.fullmatch(s):
        raise ValueError(f"invalid duration: {text!r}")
    seen = set()
    total = 0
    for number, unit in _TOKEN.findall(s):
        unit = unit.lower()
        if unit not in _UNITS or unit in seen:
            raise ValueError(f"invalid or repeated unit: {unit!r}")
        seen.add(unit)
        total += int(number) * _UNITS[unit]
    return total
