# A rebind of a record local first declared inside a BLOCK (if / loop body)
# takes a slot declared before the `if` / `while` statement: the superseded
# value drops at the block's closing brace, the last one at function exit,
# and a later pass of a loop drops the previous pass's last value at its
# rebind (CPython drops the superseded one at the rebind, a timing Python
# does not promise). See no_cpython.txt.
from tpy import int32


class Noisy:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def __del__(self) -> None:
        print("drop", self.name)


def in_if(flag: bool) -> None:
    print("enter in_if")
    if flag:
        r = Noisy("if-first")
        r = Noisy("if-second")
        print("inside:", r.name)
    print("after block")


def in_loop() -> None:
    print("enter in_loop")
    i = 0
    while i < 2:
        r = Noisy("loop-first")
        r = Noisy("loop-second")
        print("inside:", r.name)
        i += 1
    print("after loop")


def main() -> None:
    in_if(True)
    print("---")
    in_loop()
    print("---")
    print("done")


main()
