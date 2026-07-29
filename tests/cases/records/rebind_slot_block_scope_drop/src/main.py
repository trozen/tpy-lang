# A rebind slot reserved inside a BLOCK (if / loop body) is declared at the
# function prologue, so the value it holds outlives the block. Pins the CURRENT
# drop timing, which is wrong: the superseded value should drop at the rebind,
# as CPython does. See no_cpython.txt for the tracked cause.
from tpy import Int32


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
