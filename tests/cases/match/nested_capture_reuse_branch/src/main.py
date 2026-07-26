# The nested `match` that reuses a capture name need not sit at the arm's top
# level: buried under an `if` (or any compound statement) it still rebinds the
# outer capture, so the detector must walk sub-bodies, not just arm statements.
class Cat:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives


def pick(a: Cat, b: Cat, flag: bool) -> int:
    match a:
        case Cat(lives=v):
            if flag:
                match b:
                    case Cat(lives=v):   # tpyc: ok
                        pass
            return v
    return -1


def main() -> None:
    print(pick(Cat(1), Cat(2), True))    # 2 -- the nested bind ran
    print(pick(Cat(1), Cat(2), False))   # 1 -- it did not


main()
