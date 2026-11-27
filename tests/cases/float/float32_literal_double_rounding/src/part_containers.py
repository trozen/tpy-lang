# Containers, Optional, union and Own slots: the element and payload a float32
# slot stores is the literal's double converted, and a lookup needle too.
from tpy import Own, float32


def opt_param(x: float32 | None) -> float:
    return float(float32(x)) if x is not None else -1.0


def own_take(x: Own[float32]) -> float:
    return float(float32(x))


def opt_ret() -> float32 | None:
    return 1.0000000596046448  # tpyc: ok


def main() -> None:
    # append, then the same literal as an index / count needle
    xs: list[float32] = []
    xs.append(1.0000000596046448)  # tpyc: ok
    print("append", float(float32(xs[0])))
    print("index", xs.index(1.0000000596046448), "count", xs.count(1.0000000596046448))
    # set element and membership needle
    s: set[float32] = {1.0000000596046448}  # tpyc: ok
    print("set_in", 1.0000000596046448 in s)
    # Optional local, reassign, parameter and return
    o: float32 | None = 1.0000000596046448  # tpyc: ok
    print("opt_local", float(float32(o)) if o is not None else -1.0)
    o = 1.0000000596046448  # tpyc: ok
    print("opt_reassign", float(float32(o)) if o is not None else -1.0)
    print("opt_param", opt_param(1.0000000596046448))
    r = opt_ret()
    print("opt_return", float(float32(r)) if r is not None else -1.0)
    # union member
    u: float32 | str = 1.0000000596046448  # tpyc: ok
    if not isinstance(u, str):
        print("union", float(float32(u)))
    # comprehension element
    cs: list[float32] = [1.0000000596046448 for _ in range(2)]  # tpyc: ok
    print("comprehension", float(float32(cs[1])))
    # Own slots: insert, set add, setdefault, an Own[float32] argument
    ys: list[float32] = [1.5]
    ys.insert(0, 1.0000000596046448)  # tpyc: ok
    print("own_insert", float(float32(ys[0])))
    t: set[float32] = set()
    t.add(1.0000000596046448)  # tpyc: ok
    print("own_set_add", [float(float32(x)) for x in t])
    e: dict[str, float32] = {}
    e.setdefault("k", 1.0000000596046448)  # tpyc: ok
    print("own_setdefault", float(float32(e["k"])))
    print("own_arg", own_take(1.0000000596046448))  # tpyc: ok
