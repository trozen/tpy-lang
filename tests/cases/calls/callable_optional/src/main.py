# Callable | None (optional callbacks): field form + param form (lambda /
# function-by-name / None default, including inside a generator).
from typing import Callable, Iterator
from tpy import int32

class Emitter:
    on_event: Callable[[str], None] | None

    def __init__(self) -> None:
        self.on_event = None

    def set_handler(self, cb: Callable[[str], None]) -> None:
        self.on_event = cb

    def emit(self, msg: str) -> None:
        if self.on_event is not None:
            self.on_event(msg)

    def __str__(self) -> str:
        return "Emitter(...)"

def run(n: int32, hook: Callable[[int32], None] | None = None) -> None:
    if hook is not None:
        hook(n)


# Non-void return: the narrowed optional callable's result is used.
def apply(n: int32, f: Callable[[int32], int32] | None = None) -> int32:
    if f is not None:
        return f(n)
    return n


def report(code: int32) -> None:
    print("report:", code)


def triple(x: int32) -> int32:
    return x * 3


def scan(n: int32, onerror: Callable[[int32], None] | None = None) -> Iterator[int32]:
    i = 0
    while i < n:
        if i == 1 and onerror is not None:
            onerror(i)
        yield i
        i += 1


def main() -> None:
    e = Emitter()
    e.emit("ignored")
    e.set_handler(lambda s: print("got:", s))
    e.emit("hello")
    e.emit("world")

    run(1, lambda c: print("lambda:", c))   # lambda arg
    run(2, report)                          # function-by-name arg
    run(3)                                  # None default

    print("apply-lambda:", apply(5, lambda x: x + 100))
    print("apply-name:", apply(5, triple))
    print("apply-none:", apply(5))

    for v in scan(3, report):
        print("scan:", v)
    for v in scan(2):
        print("scan-none:", v)

main()
