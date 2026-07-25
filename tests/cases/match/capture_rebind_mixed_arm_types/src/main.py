# A capture NAME bound at different types per arm keeps its per-arm binding
# even when one arm rebinds it: the rebound-capture hoist is skipped unless
# every binding arm agrees on the type, since one shared slot cannot hold both.
class Cat:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives


class Dog:
    nick: str
    def __init__(self, nick: str) -> None:
        self.nick = nick


def describe(a: Cat | Dog) -> str:
    match a:
        case Cat(lives=v):
            return str(v)      # v is int here
        case Dog(nick=v):
            v += "!"           # tpyc: ok -- v is str here, and rebound
            return v
    return "?"


def main() -> None:
    print(describe(Cat(9)))
    print(describe(Dog("Rex")))


main()
