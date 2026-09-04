# A generator expression over a STR source: the chars iterate off the same
# begin/end pair a container does. The module-global source also pins that the
# IIFE names the global directly -- capturing a namespace-scope object would be
# ill-formed C++.
LETTERS = "abc"


def from_global() -> None:
    d = dict(((str(x), 0.0) for x in LETTERS))  # tpyc: ok
    d["a"] += 1.0
    print(len(d), d["a"], d["c"])


def from_local() -> None:
    letters = "xy"
    scale = 2.0
    # The element expression reads an ordinary local, which IS captured.
    d = dict(((str(c), scale) for c in letters))  # tpyc: ok
    print(len(d), d["x"], d["y"])


def main() -> None:
    from_global()
    from_local()


main()
