# A nested match rebinding an outer arm's capture name at a DIFFERENT type is
# rejected: both binds share one hoisted slot (a `match` is not its own scope),
# and C++ would silently truncate wherever an implicit conversion exists.
from tpy import Int32, Int64


class Cat:
    lives: Int32
    def __init__(self, lives: Int32) -> None:
        self.lives = lives


class Dog:
    lives: Int64
    def __init__(self, lives: Int64) -> None:
        self.lives = lives


def pick(a: Cat, b: Dog) -> Int32:
    match a:
        case Cat(lives=v):
            match b:
                case Dog(lives=v):  # tpyc: error(/bound as 'Int32'.*and 'Int64'/)
                    pass
            return v
    return Int32(-1)


def main() -> None:
    print(pick(Cat(Int32(1)), Dog(Int64(5000000000))))


main()
