import re

_NUM = re.compile(r"0|[1-9]\d*")
_IDENT = re.compile(r"[0-9A-Za-z-]+")


def _parse(version):
    if not isinstance(version, str):
        raise TypeError("version must be a string")
    main, plus, build = version.partition("+")
    if plus and (not build or not all(_IDENT.fullmatch(p) for p in build.split("."))):
        raise ValueError(f"bad build metadata: {version!r}")
    core, dash, pre = main.partition("-")
    parts = core.split(".")
    if len(parts) != 3 or not all(_NUM.fullmatch(p) for p in parts):
        raise ValueError(f"bad version core: {version!r}")
    idents = []
    if dash:
        for ident in pre.split("."):
            if not _IDENT.fullmatch(ident):
                raise ValueError(f"bad prerelease: {version!r}")
            if ident.isdigit():
                if not _NUM.fullmatch(ident):
                    raise ValueError(f"leading zero in prerelease: {version!r}")
                idents.append((0, int(ident), ""))
            else:
                idents.append((1, 0, ident))
    return tuple(int(p) for p in parts), dash != "", idents


def compare_versions(a, b):
    core_a, has_pre_a, pre_a = _parse(a)
    core_b, has_pre_b, pre_b = _parse(b)
    if core_a != core_b:
        return -1 if core_a < core_b else 1
    if has_pre_a != has_pre_b:
        return 1 if not has_pre_a else -1  # a release outranks its prereleases
    if pre_a == pre_b:
        return 0
    return -1 if pre_a < pre_b else 1
