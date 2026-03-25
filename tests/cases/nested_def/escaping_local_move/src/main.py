# Test escaping closure with local non-value object at last use: moved, no warning
from typing import Callable
from tpy import Int32

class Config:
    value: Int32
    def __init__(self, v: Int32) -> None:
        self.value = v

def make_getter() -> Callable[[], Int32]:
    cfg = Config(42)
    def get_value() -> Int32:  # tpyc: ok
        return cfg.value
    return get_value

def main() -> None:
    getter = make_getter()
    print(getter())

main()
