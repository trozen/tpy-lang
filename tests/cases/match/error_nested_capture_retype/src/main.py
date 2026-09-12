# A nested match rebinding an outer arm's capture name at a DIFFERENT type is
# rejected: both binds share one hoisted slot (a `match` is not its own scope),
# and C++ would silently truncate wherever an implicit conversion exists.
from tpy import int32, int64


class Cat:
    lives: int32
    def __init__(self, lives: int32) -> None:
        self.lives = lives


class Dog:
    lives: int64
    def __init__(self, lives: int64) -> None:
        self.lives = lives


def pick(a: Cat, b: Dog) -> int32:
    match a:
        case Cat(lives=v):
            match b:
                case Dog(lives=v):  # tpyc: error(/bound as 'int32'.*and 'int64'/)
                    pass
            return v
    return int32(-1)


def main() -> None:
    print(pick(Cat(int32(1)), Dog(int64(5000000000))))


main()
