# A two-lvalue ternary passed to an Own[T] (non-value) param: it binds as an
# lvalue reference the Own slot's T&& can't take, so the chosen arm is copied
# into the slot (warned -- acknowledged divergence: CPython would alias the
# argument). Pre-fix this was a hard g++ error. Output observes only the owned
# value (11 either way) to stay parity-clean.
from tpy import Own


class Box:
    val: int
    def __init__(self, v: int) -> None:
        self.val = v


def take(b: Own[Box]) -> Own[Box]:
    b.val += 1
    return b


def main() -> None:
    a = Box(10)
    other = Box(20)
    flag = True
    r = take(a if flag else other)   # tpyc: warning(/copies Box into owned storage/)
    print(r.val)                     # 11 (owned value, bumped)


main()
