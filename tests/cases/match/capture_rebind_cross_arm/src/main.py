# One arm rebinds a capture name that a sibling arm binds (same type) without
# rebinding: the hoist is collected across arms, so both arms bind the one
# hoisted slot by assignment and each arm's value flows out correctly.
class Cat:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives


class Dog:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives


def score(a: Cat | Dog) -> int:
    match a:
        case Cat(lives=v):
            v = v + 100    # tpyc: ok -- rebinding arm
        case Dog(lives=v):
            pass           # same name, same type, NOT rebound
    return v


def main() -> None:
    print(score(Cat(9)))   # 109
    print(score(Dog(7)))   # 7 -- the non-rebinding arm's value still flows out


main()
