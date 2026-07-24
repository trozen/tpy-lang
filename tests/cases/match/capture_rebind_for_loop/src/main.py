# A value-typed match capture (int field) rebound in its arm binds BY VALUE (a
# fresh copy) rather than aliasing the matched object: the for-loop var and
# aug-assign rebinds mutate the local, leaving the subject's field untouched --
# matching CPython, where the capture is a fresh local. Regression guard for
# the `auto& v = subject.lives` alias write-through miscompile (was TPy `12 3`
# vs CPython `12 9`).
class Cat:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives


def use_for(a: Cat) -> int:
    match a:
        case Cat(lives=v):
            before = v            # read the capture before the rebind
            xs: list[int] = [1, 2, 3]
            for v in xs:
                pass
            return before + v     # 9 + 3
    return -1


def use_aug(a: Cat) -> int:
    match a:
        case Cat(lives=v):
            v += 100
            return v              # 9 + 100
    return -1


def main():
    c = Cat(9)
    print(use_for(c), c.lives)      # 12 9 -- field untouched by the loop rebind
    d = Cat(9)
    print(use_aug(d), d.lives)      # 109 9


main()
