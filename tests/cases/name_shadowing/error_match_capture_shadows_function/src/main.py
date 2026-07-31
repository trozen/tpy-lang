# A match capture makes its name a function-local for the arms after it, so a
# later arm cannot reach the module function of that name -- CPython raises
# UnboundLocalError for the same source. Registering captures for name
# resolution is what makes this reachable; before, the later arm silently called
# the module function. The diagnostic names callability rather than the
# shadowing, which is tracked in BUGS.md.
from tpy import Int32


def helper() -> Int32:
    return 42


class Cat:
    def __init__(self, lives: Int32):
        self.lives = lives


class Dog:
    pass


def pick(v: Cat | Dog) -> Int32:
    match v:
        case Cat(lives=helper):
            return helper
        case Dog():
            return helper()  # tpyc: error(/'helper' is not callable/)


def main() -> None:
    print(pick(Dog()))


main()
