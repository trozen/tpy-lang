# An uncaught SystemExit with a str code prints that code to stderr and exits
# with status 1 after unwinding. The status reads `code`, not str(e): a
# subclass's own __str__ does not change what is printed.
import sys


class Fatal(SystemExit):  # tpyc: warning(/hides .BaseException.__str__./)
    def __str__(self) -> str:
        return "custom str, not printed"


class Loud:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __del__(self) -> None:
        print(self.tag + ": __del__")


def main() -> None:
    d = Loud("main local")
    try:
        print("main: exiting")
        # The subject: stderr gets the code, the status is 1.
        raise Fatal("fatal: bad config")
    finally:
        print("main: finally")
    print("main: not reached", d.tag)


main()
