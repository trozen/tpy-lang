# Truthiness of an un-narrowed pointer-repr Optional[record] is CPython's
# `x is not None and bool(x)`: null-check AND the inner record's
# __bool__/__len__ (regression: it emitted only the non-null pointer test, so
# a non-None but falsy record read truthy). Covers if / not / and / while and
# a side-effecting operand (the call must evaluate exactly once). A value-repr
# str|None control stays on the is_truthy render.
#
# A storage-form Optional[record] source (record field / container element) is
# std::optional, not a T*, so it must NOT take the ptr_truthy dispatch (that
# would fail to compile). field_test / elem_test guard that it still builds
# (they use only None / truthy values -- the falsy-non-None dunder-dispatch gap
# on storage-form sources is a separate open shape, see BUGS.md).


class Flag:
    def __init__(self, on: bool):
        self.on = on

    def __bool__(self) -> bool:
        return self.on


class Holder:
    f: Flag | None

    def __init__(self, f: Flag | None):
        self.f = f


class Bag:
    def __init__(self, n: int):
        self.n = n

    def __len__(self) -> int:
        return self.n


class Counter:
    n: int

    def __init__(self):
        self.n = 0


def if_test(c: Flag | None) -> int:
    if c:
        return 1
    return 0


def not_test(c: Flag | None) -> int:
    if not c:
        return 10
    return 11


def and_test(c: Flag | None, d: Flag | None) -> int:
    if c and d:
        return 20
    return 21


def while_test(c: Flag | None) -> int:
    x = c
    n = 0
    while x:
        n += 1
        x = None
    return n


def len_test(b: Bag | None) -> int:
    if b:
        return 1
    return 0


def observe(cnt: Counter, f: Flag) -> Flag | None:
    cnt.n += 1
    return f


def call_test(f: Flag) -> int:
    cnt = Counter()
    if observe(cnt, f):  # side-effecting operand -> evaluate once
        r = 1
    else:
        r = 0
    return r * 100 + cnt.n


def str_test(s: str | None) -> int:
    if s:
        return 1
    return 0


def field_test(h: Holder) -> int:
    # Storage-form field (std::optional): must compile via the non-null test,
    # never the pointer-only ptr_truthy dispatch.
    if h.f:
        return 1
    return 0


def elem_test(xs: list[Flag | None]) -> int:
    # Storage-form container element (std::optional): same compile guard.
    if xs[0]:
        return 1
    return 0


def main():
    print(if_test(None), if_test(Flag(False)), if_test(Flag(True)))
    print(not_test(None), not_test(Flag(False)), not_test(Flag(True)))
    print(and_test(Flag(False), Flag(True)), and_test(Flag(True), Flag(True)),
          and_test(None, Flag(True)))
    print(while_test(None), while_test(Flag(False)), while_test(Flag(True)))
    print(len_test(None), len_test(Bag(0)), len_test(Bag(2)))
    print(call_test(Flag(False)), call_test(Flag(True)))
    print(str_test(None), str_test(""), str_test("x"))
    print(field_test(Holder(None)), field_test(Holder(Flag(True))))
    print(elem_test([None]), elem_test([Flag(True)]))


main()
