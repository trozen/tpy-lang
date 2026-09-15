# Panic through the assert-message PROPERTY arm: the message is a @property
# read, so the failing assert has to CALL the getter to build the panic text --
# the getter returns a computed string, so the panic line proves it ran.
from tpy import int32


class Error:
    message: str

    def __init__(self, message: str) -> None:
        self.message = message

    @property
    def detail(self) -> str:
        return "detail:" + self.message


def check(n: int32, e: Error) -> int32:
    assert n > 0, e.detail  # tpyc: ok
    return n


def main() -> None:
    e = Error("bad value")
    print(check(-1, e))


main()
