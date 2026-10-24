# A call through an Optional callable field whose argument clears the field
# raises TypeError instead of calling a destroyed function.
from typing import Callable


class H:
    cb: Callable[[int], int] | None

    def __init__(self) -> None:
        self.cb = None


def times10(v: int) -> int:
    return v * 10


def clear(h: H) -> int:
    print("argument ran")
    h.cb = None
    return 2


def main() -> None:
    h = H()
    h.cb = times10
    try:
        # The callee was present when the call began; the argument clears it.
        print(h.cb(clear(h)))  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("clear:", e)


main()
