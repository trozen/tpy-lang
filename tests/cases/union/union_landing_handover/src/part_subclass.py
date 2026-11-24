# A value-type subclass object handed over as its base: copied while it
# lives, moved at its last use.
from tpy import ValueType, int32


class VBase(ValueType):
    s: str

    def __init__(self, s: str) -> None:
        self.s = s


class VSub(VBase, ValueType):
    # Sets `s` itself: the sections pin the base part handed over, not a
    # VBase constructor run on the way.
    def __init__(self, s: str) -> None:  # tpyc: warning(/does not call 'super/)
        self.s = s


def take(u: VBase | int32) -> None:
    if isinstance(u, VBase):
        print("positions-vsub-take", u.s)


def take_twin(u: VBase) -> None:
    print("positions-vsub-twin", u.s)


# a live value-type object is copied into the base, never moved out of:
# at a union parameter and declaration, and at the scalar twin
def live_value() -> None:
    v = VSub("a-string-long-enough-to-own-a-heap-buffer")
    take(v)  # tpyc: ok
    take_twin(v)  # tpyc: ok
    u: VBase | int32 = v  # tpyc: warning(/narrows 'VSub'/)
    print("positions-vsub-kept", v.s, isinstance(u, int32))


def show_v(tag: str, u: VBase | int32) -> None:
    if isinstance(u, VBase):
        print(tag, u.s)


def show_v_twin(tag: str, u: VBase) -> None:
    print(tag, u.s)


# an element, a dict value and a select over two names are live objects
# too, at the union parameter and at the scalar twin
def live_reads(flag: bool) -> None:
    xs = [VSub("an-element-string-long-enough-to-own-a-buffer")]
    show_v("positions-vsub-elem", xs[0])  # tpyc: ok
    show_v_twin("positions-vsub-elem-twin", xs[0])  # tpyc: ok
    d = {"k": VSub("a-dict-value-string-long-enough-to-own-one")}
    show_v("positions-vsub-dict", d["k"])  # tpyc: ok
    show_v_twin("positions-vsub-dict-twin", d["k"])  # tpyc: ok
    a = VSub("a-left-arm-string-long-enough-to-own-a-buffer")
    b = VSub("a-right-arm-string-long-enough-to-own-buffer")
    show_v("positions-vsub-select", a if flag else b)  # tpyc: ok
    show_v_twin("positions-vsub-select-twin", a if flag else b)  # tpyc: ok
    print("positions-vsub-reads-kept", xs[0].s, d["k"].s, a.s, b.s)


# the returned local at its last use is moved into the base
def last_use() -> VBase | int32:
    v = VSub("a-returned-string-long-enough-to-own-a-buffer")
    return v  # tpyc: warning(/narrows 'VSub'/)


def last_use_twin() -> VBase:
    v = VSub("a-returned-twin-string-long-enough-to-own-one")
    return v  # tpyc: warning(/narrows 'VSub'/)


def main() -> None:
    live_value()
    live_reads(True)
    live_reads(False)
    show_v("positions-vsub-last-use", last_use())
    show_v_twin("positions-vsub-last-use-twin", last_use_twin())
