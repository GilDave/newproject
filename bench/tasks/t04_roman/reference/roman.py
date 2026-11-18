import re

_PAIRS = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
          (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
_STANDARD = re.compile(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})")


def to_roman(n):
    if not isinstance(n, int) or isinstance(n, bool):
        raise TypeError("n must be an integer")
    if not 1 <= n <= 3999:
        raise ValueError("n must be between 1 and 3999")
    out = []
    for value, symbol in _PAIRS:
        while n >= value:
            out.append(symbol)
            n -= value
    return "".join(out)


def from_roman(s):
    if not isinstance(s, str) or not s or not _STANDARD.fullmatch(s):
        raise ValueError(f"not a standard roman numeral: {s!r}")
    total = 0
    for value, symbol in _PAIRS:
        while s.startswith(symbol):
            total += value
            s = s[len(symbol):]
    return total
