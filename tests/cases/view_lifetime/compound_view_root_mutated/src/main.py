# A str/bytes local from a COMPOUND view source (ternary / and-or / nested) over
# owned-borrow arms must register every root, so mutating any root after the
# binding demotes the view to an owned copy instead of dangling (was a silent UAF).
# Each function mutates a root and returns/reads the value; a dangling view would
# read freed memory and diverge from CPython.


class Rec:
    s: str

    def __init__(self, s: str) -> None:
        self.s = s


def ternary(c: list[str], d: list[str], cond: bool) -> str:
    x = c[0] if cond else d[0]  # tpyc: type(str)
    c.append("padding long enough to force the backing vector to reallocate")
    d.append("padding long enough to force the backing vector to reallocate")
    return x


def or_chain(c: list[str], d: list[str]) -> str:
    x = c[0] or d[0]  # tpyc: type(str)
    c.append("padding long enough to force the backing vector to reallocate")
    return x


def and_chain(c: list[str], d: list[str]) -> str:
    x = c[0] and d[0]  # tpyc: type(str)
    c.append("padding long enough to force the backing vector to reallocate")
    d.append("padding long enough to force the backing vector to reallocate")
    return x


def nested(c: list[str], d: list[str], e: list[str], cond: bool) -> str:
    x = (c[0] if cond else d[0]) or e[0]  # tpyc: type(str)
    d.append("padding long enough to force the backing vector to reallocate")
    return x


def field_arm(r: Rec, t: Rec, cond: bool) -> str:
    x = r.s if cond else t.s  # tpyc: type(str)
    r.s = "padding long enough to force the std::string buffer to reallocate"
    return x


def bytes_ternary(c: list[bytes], d: list[bytes], cond: bool) -> int:
    x = c[0] if cond else d[0]  # tpyc: type(bytes)
    c.append(b"padding long enough to force the backing vector to reallocate")
    return len(x)


def main() -> None:
    print(ternary(["alpha"], ["beta"], True))
    print(or_chain(["gamma"], ["delta"]))
    print(and_chain(["epsilon"], ["zeta"]))
    print(nested(["one"], ["two"], ["three"], False))
    print(field_arm(Rec("eta"), Rec("theta"), True))
    print(bytes_ternary([b"abcd"], [b"ef"], True))


main()
