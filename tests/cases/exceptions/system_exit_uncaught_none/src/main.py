# An uncaught sys.exit() ends the program with status 0 once the stack has
# unwound: the finally body, the with exit and the destructors of every frame
# it leaves run first, innermost first.
import sys


class Loud:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __del__(self) -> None:
        print(self.tag + ": __del__")


class Guard:
    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("work: __exit__ sees an exception", exc_val is not None)


def work() -> None:
    d = Loud("work local")
    with Guard():
        try:
            print("work: exiting")
            # The subject: no handler anywhere, so the exit status is 0.
            sys.exit()
        finally:
            print("work: finally")
    print("work: not reached", d.tag)


def main() -> None:
    keep = Loud("main local")
    work()
    print("main: not reached", keep.tag)


main()
