# StrView local bound from a call whose return is safe (param-derived, direct
# call return, or StrView(param) constructor) is itself safe to return.
# Regression: the provenance/trusted-call-return tracker previously skipped
# value types, so `sv = f(...); return sv` was rejected even though
# `return f(...)` was accepted on the same path.
from tpy import StrView

def pick(s: str) -> StrView:
    return s

def indirect_annot(text: str) -> StrView:
    sv: StrView = pick(text)
    return sv

def indirect_inferred(text: str) -> StrView:
    sv = pick(text)
    return sv

def reassigned(a: str, b: str) -> StrView:
    sv: StrView = pick(a)
    sv = pick(b)
    return sv

def from_ctor(s: str) -> StrView:
    sv: StrView = StrView(s)
    return sv

def conditional_rebind(a: str, b: str, flag: bool) -> StrView:
    sv: StrView = pick(a)
    if flag:
        sv = pick(b)
    return sv

def ternary_params(a: str, b: str, flag: bool) -> StrView:
    sv: StrView = a if flag else b
    return sv

def loop_iter(items: list[str], default: str) -> StrView:
    sv: StrView = default
    for item in items:
        sv = item
    return sv

def main() -> None:
    print(indirect_annot("hello"))
    print(indirect_inferred("world"))
    print(reassigned("foo", "bar"))
    print(from_ctor("baz"))
    print(conditional_rebind("aa", "bb", True))
    print(conditional_rebind("cc", "dd", False))
    print(ternary_params("yes", "no", True))
    print(loop_iter(["a", "b", "c"], "d"))
    print(loop_iter([], "empty"))

main()
