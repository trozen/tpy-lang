# An owning str / String / bytes field takes any source of its family, at the
# constructor's member-init list and at a method field write alike.
# Copy semantics are intended at every boundary (str/bytes are immutable).
# A StrView field keeps only views of storage it does not own (see Viewer).
from tpy import StrView, String


class Owner:
    name: str
    data: bytes

    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self.data = data


class Tags:
    full: str
    conv: str
    fmt: str
    pick: str
    opt: str
    tail: str
    twice: str
    upper: str
    from_field: str
    echo: str

    def __init__(self, a: str, x: int, o: Owner, m: str | None) -> None:
        self.full = a + "!"  # tpyc: ok
        self.conv = "p" + str(x)  # tpyc: ok
        self.fmt = f"{a}-{x}"  # tpyc: ok
        self.pick = a if x > 0 else "z"  # tpyc: ok
        self.opt = m if m is not None else "none"  # tpyc: ok
        self.tail = a[1:]  # tpyc: ok
        self.twice = a * 2  # tpyc: ok
        self.upper = a.upper() + "!"  # tpyc: ok
        self.from_field = o.name  # tpyc: ok
        # Reads a field an earlier member-init set.
        self.echo = self.full + "?"  # tpyc: ok

    def reset(self, a: str, x: int, o: Owner) -> None:
        # Method-position twins of the member-init shapes.
        self.fmt = f"{a}+{x}"  # tpyc: ok
        self.pick = a if x > 0 else "z"  # tpyc: ok
        self.from_field = o.name  # tpyc: ok


class Blob:
    joined: bytes
    copied: bytes
    from_field: bytes

    def __init__(self, src: bytes, o: Owner) -> None:
        self.joined = src + b"!"  # tpyc: ok
        self.copied = bytes(src)  # tpyc: ok
        self.from_field = o.data  # tpyc: ok

    def reset(self, o: Owner) -> None:
        self.from_field = o.data + b"?"  # tpyc: ok


class Walrus:
    s: str
    n: int
    seen: str

    def __init__(self, a: str, x: int) -> None:
        # A walrus binds a body local, so these inits run in the ctor body.
        self.s = (b := a) + "!"  # tpyc: ok
        self.n = (y := x) + 1  # tpyc: ok
        self.seen = b + str(y)


class Owned:
    s: String

    def __init__(self, a: str) -> None:
        self.s = a + "!"  # tpyc: ok


def first(a: str) -> StrView:
    return a


class Viewer:
    v: StrView

    def __init__(self, a: str) -> None:
        self.v = a

    def retarget(self, a: str, pick: int) -> None:
        # A StrView field takes a literal, a param, a slice of one, or a
        # view-returning call -- never a fresh str.
        if pick == 0:
            self.v = "lit"  # tpyc: ok
        elif pick == 1:
            self.v = a  # tpyc: ok
        elif pick == 2:
            self.v = a[1:]  # tpyc: ok
        else:
            self.v = first(a)  # tpyc: ok


def main() -> None:
    o = Owner("n", b"d")
    t = Tags("q", 3, o, None)
    o.name = "changed"
    print("ctor:", t.full, t.conv, t.fmt, t.pick, t.opt, t.tail, t.twice,
          t.upper, t.from_field, t.echo)
    t2 = Tags("r", 0, o, "m")
    print("ctor2:", t2.pick, t2.opt, t2.from_field)
    t.reset("w", -1, o)
    o.name = "again"
    print("method:", t.fmt, t.pick, t.from_field)

    b = Blob(b"ab", o)
    o.data = b"changed"
    print("bytes:", b.joined, b.copied, b.from_field)
    b.reset(o)
    print("bytes method:", b.from_field)

    w = Walrus("k", 4)
    print("walrus:", w.s, w.n, w.seen)

    print("String:", Owned("s").s)

    vw = Viewer("init")
    for pick in range(4):
        vw.retarget("view", pick)
        print("StrView method:", pick, vw.v)


main()
