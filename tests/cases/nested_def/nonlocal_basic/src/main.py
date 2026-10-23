# Test nonlocal for mutable captures, and a nonlocal store into a parameter
# and into a str local of the enclosing function.
from tpy import int32

def main() -> None:
    total: int32 = 0
    def accumulate(x: int32) -> None:
        nonlocal total
        total += x
    accumulate(10)
    accumulate(20)
    accumulate(30)
    print(total)
    k = 4
    print("param", bump(k), k)
    print("str", grow())
    print("str-static", relabel())
    print("view-holder", view_holder())

# param: the closure rebinds the enclosing function's parameter, which then
# holds storage of its own instead of the caller's.
def bump(n: int) -> int:
    def step() -> None:
        nonlocal n
        n = n + 1  # tpyc: ok
    step()
    step()
    return n

# str: the store concatenates, so the enclosing local owns its text.
def grow() -> str:
    s = "a"  # tpyc: type(str)
    def more() -> None:
        nonlocal s
        s = s + "b"  # tpyc: ok
    more()
    more()
    return s

# str-static: a literal store keeps the enclosing local a view.
def relabel() -> str:
    s = "a"  # tpyc: type(StrView)
    def change() -> None:
        nonlocal s
        s = "b"
    change()
    return s


class N:
    def __init__(self, s: str) -> None:
        self.s = s

# view-holder: the store reads text out of a tuple local to the closure, so
# the enclosing local owns a copy that outlives the closure's frame.
def view_holder() -> str:
    v = "start"  # tpyc: type(str)
    def inner() -> None:
        nonlocal v
        t = (N("hello" + str(len(v))), 5)
        v = t[0].s  # tpyc: ok
    inner()
    return v

main()
