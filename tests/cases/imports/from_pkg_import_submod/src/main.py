# `from pkg import submod` binds submod as a usable namespace:
# - call qualifying:    submod.fn(...)
# - record qualifying:  submod.RecordName(...)
# - annotation typing:  field: submod.RecordName
# Pre-fix: each of these required an explicit `from pkg.submod import X`
# (or `pkg.submod.X` annotations, which sema rejected outright).
from _bindings import pcre2
from tpy import int32


def use(c: pcre2.Code) -> int32:
    return c.n


def main() -> None:
    print(pcre2.compile_pattern("hello"))
    print(use(pcre2.Code(int32(7))))


main()
