# A BigInt membership needle against a fixed-int-keyed dict answers by
# VALUE: an out-of-declared-width needle is simply absent (False, like
# CPython) -- never a range panic. Sequences compare by equality and were
# already correct (inverse guard).
from tpy import int32, int64, uint32


def main():
    d: dict[int32, str] = {1: "a", 2: "b"}
    k: int = 2
    print(k in d)
    k3: int = 3
    print(k3 in d)
    big: int = 1099511627776  # 2**40
    print(big in d)
    neg: int = -1099511627776  # -(2**40)
    print(neg in d)
    print(big not in d)
    print(big in d.keys())
    counts: dict[str, int32] = {"a": 1}
    print(big in counts.values())
    one: int = 1
    print(one in counts.values())
    d64: dict[int64, str] = {}
    d64[big] = "wide"
    print(big in d64)
    huge: int = 1180591620717411303424  # 2**70
    print(huge in d64)
    b63: int = 9223372036854775808  # 2**63: one past int64 max
    print(b63 in d64)
    du: dict[uint32, str] = {7: "u"}
    seven: int = 7
    print(seven in du)
    print(neg in du)
    print(big in du)
    ucounts: dict[str, uint32] = {"a": 7}
    print(seven in ucounts.values())
    print(neg in ucounts.values())
    xs: list[int32] = [1, 2]
    print(big in xs)
    print(k in xs)


main()
