# A nested `match` reusing an outer arm's capture name REBINDS it: a `match` is
# not its own scope in Python, so the inner capture writes the same function
# local and its value flows out of the outer arm.
class Cat:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives


def pick(a: Cat, b: Cat) -> int:
    match a:
        case Cat(lives=v):
            match b:
                case Cat(lives=v):   # tpyc: ok -- rebinds the outer v
                    pass
            return v                 # 2, not 1
    return -1


def relabel(a: Cat, b: Cat) -> str:
    # Same shape one level deeper: the innermost bind is what every enclosing
    # arm reads, so the third `case` -- back on `a` -- is what flows out.
    match a:
        case Cat(lives=v):
            match b:
                case Cat(lives=v):
                    match a:
                        case Cat(lives=v):
                            pass
            return str(v)
    return "?"


def main() -> None:
    print(pick(Cat(1), Cat(2)))
    print(relabel(Cat(1), Cat(2)))


main()
