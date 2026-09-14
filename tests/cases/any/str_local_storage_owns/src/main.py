# A str / bytes LOCAL stored into Any owns a copy under the family's owned
# typeid, and a fixed-int / float ctor over a literal keeps its width.

from typing import Any, cast
from tpy import int64, uint8, float32, String


class H:
    slot: Any

    def __init__(self) -> None:
        self.slot = 0

    # method position
    def local_in_method(self) -> str:
        v = "m"
        a: Any = v
        return cast(str, a)


def take(a: Any) -> Any:
    return a


def ret_local() -> Any:
    v = "rl"
    return v


def take_str(s: str) -> Any:
    a: Any = s
    return a


def mk() -> str:
    return "made"


def main() -> None:
    # str local, inferred and annotated
    v = "x"
    a: Any = v  # tpyc: ok
    print("local", cast(str, a))
    w: str = "y"
    b: Any = w  # tpyc: ok
    print("annotated", cast(str, b))
    if isinstance(a, str):
        print("isinstance", a)

    # list[Any] literal element and append
    xs: list[Any] = [v]  # tpyc: ok
    xs.append(w)  # tpyc: ok
    print("list", cast(str, xs[0]), cast(str, xs[1]))

    # dict[str, Any] literal value and setitem
    d: dict[str, Any] = {"k": v}  # tpyc: ok
    d["j"] = w  # tpyc: ok
    print("dict", cast(str, d["k"]), cast(str, d["j"]))

    # Any param, return at -> Any
    print("param", cast(str, take(v)))  # tpyc: ok
    print("return", cast(str, ret_local()))

    # Any field write (no read-back: an Any FIELD read does not lower yet,
    # so the owned spelling is pinned by the generated C++ alone)
    h = H()
    h.slot = v  # tpyc: ok
    print("method", h.local_in_method())

    # bytes local
    bb = b"by"
    c: Any = bb  # tpyc: ok
    print("bytes", cast(bytes, c))
    if isinstance(c, bytes):
        print("bytes-isinstance", len(c))

    # a fixed-int / float ctor over a literal folds to the bare literal; the
    # cell still holds the ctor's width
    i: Any = int64(1)  # tpyc: ok
    u: Any = uint8(3)  # tpyc: ok
    f: Any = float32(1.5)  # tpyc: ok
    print("scalars", cast(int64, i), cast(uint8, u), cast(float32, f))

    # inverses that already owned: literal, param, String, slice, call
    lit: Any = "lit"
    print("literal", cast(str, lit))
    print("str-param", cast(str, take_str("p")))
    owned: Any = String("own")
    print("String", cast(str, owned))
    sl: Any = v[0:1]
    print("slice", cast(str, sl))
    made: Any = mk()
    print("call", cast(str, made))


# module-level position
gv = "g"
ga: Any = gv  # tpyc: ok
print("global", cast(str, ga))
main()
