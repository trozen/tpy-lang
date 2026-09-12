# A str/bytes VIEW value passed to a same-family `*args` param is copied into
# the owned varargs element; owned and value-type args are unaffected.
from tpy import StrView, BytesView, int32


def joins(a: str, *parts: str) -> str:
    out = a
    for p in parts:
        out = out + p
    return out


def total_len(a: bytes, *parts: bytes) -> int32:
    n = len(a)
    for p in parts:
        n += len(p)
    return n


def addall(*nums: int32) -> int32:
    t = 0
    for n in nums:
        t += n
    return t


class Sink:
    def joins(self, a: str, *parts: str) -> str:
        out = a
        for p in parts:
            out = out + p
        return out


def reassign_view_param(p: str) -> str:
    p = joins(p, "!")     # reassign a str-view param to an owned str
    return p


def from_str_view(sv: StrView) -> str:
    return joins("x", sv)              # view arg into *parts: str


def from_bytes_view(bv: BytesView) -> int32:
    return total_len(b"x", bv)         # view arg into *parts: bytes


def loop_var_into_join(names: list[str]) -> None:
    # a str loop var (view) flowing into a varargs param
    out: list[str] = []
    for d in names:
        out.append(joins("/", d))
    for s in out:
        print(s)


def method_view_vararg(sv: StrView) -> str:
    # the method-call varargs dispatch path (distinct from free functions)
    s = Sink()
    return s.joins("m", sv)


def main() -> None:
    print(from_str_view("hi"))         # xhi
    print(reassign_view_param("p"))    # p!
    print(method_view_vararg("v"))     # mv
    print(from_bytes_view(b"hello"))   # 6
    owned = "a" + "b"
    print(joins("p", owned, "q"))      # owned + literal args (no view)
    print(addall(1, 2, 3))             # value-type varargs unaffected
    ns: list[str] = []
    ns.append("a")
    ns.append("b")
    loop_var_into_join(ns)


main()
