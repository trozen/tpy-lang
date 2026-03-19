# Test Callable | None (optional callbacks)
from typing import Callable
from tpy import Int32

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

def main() -> None:
    e = Emitter()
    e.emit("ignored")
    e.set_handler(lambda s: print("got:", s))
    e.emit("hello")
    e.emit("world")

main()
