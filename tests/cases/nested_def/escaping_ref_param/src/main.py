# Test escaping closure with non-value param: reference capture (no copy)
# The closure captures a reference to the caller's object, so mutations
# to the original are visible through the closure (Python-like semantics).
from typing import Callable
from tpy import Int32

class Config:
    value: Int32
    def __init__(self, v: Int32) -> None:
        self.value = v

def make_getter(cfg: Config) -> Callable[[], Int32]:
    def get_value() -> Int32:
        return cfg.value
    return get_value

def main() -> None:
    c = Config(10)
    getter = make_getter(c)
    print(getter())
    c.value = 42
    print(getter())

main()
