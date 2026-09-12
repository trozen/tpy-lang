# Test Callable as class field type
from typing import Callable
from tpy import int32

class Handler:
    on_event: Callable[[int32], None]

    def __init__(self, cb: Callable[[int32], None]) -> None:
        self.on_event = cb

    def trigger(self, value: int32) -> None:
        self.on_event(value)

    def __str__(self) -> str:
        return "Handler(...)"

def main() -> None:
    h = Handler(lambda x: print("event:", x))
    h.trigger(10)
    h.trigger(20)

main()
