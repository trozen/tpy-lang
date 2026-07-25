# A value-typed match capture rebound via the DECLARE-shaped forms -- a bare
# `v = ...`, a walrus, or a rebind nested in an inner block -- assigns the
# capture's own local rather than redeclaring it (CPython's `match` is not its
# own scope, so all three rebind the same local).
class Cat:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives


def nested(a: Cat, flag: bool) -> int:
    match a:
        case Cat(lives=v):
            if flag:
                v = a.lives + 100  # tpyc: ok
            return v               # 109 when flag -- the inner rebind is seen
    return -1


def top_level(a: Cat) -> int:
    match a:
        case Cat(lives=v):
            v = a.lives + 1        # tpyc: ok
            return v               # 10
    return -1


def walrus(a: Cat) -> int:
    match a:
        case Cat(lives=v):
            total = 0
            while (v := v - 1) > 0:  # tpyc: ok
                total = total + v
            return total             # 3+2+1 = 6
    return -1


def main() -> None:
    c = Cat(9)
    print(nested(c, True), nested(c, False), c.lives)  # 109 9 9
    print(top_level(Cat(9)))                           # 10
    print(walrus(Cat(4)))                              # 6


main()
