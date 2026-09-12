# Test escaping closure with non-value param: reference capture (no copy)
# The closure captures a reference to the caller's object, so mutations
# to the original are visible through the closure (Python-like semantics).
from typing import Callable
from tpy import int32

class Config:
    value: int32
    def __init__(self, v: int32) -> None:
        self.value = v

def make_getter(cfg: Config) -> Callable[[], int32]:
    def get_value() -> int32:
        return cfg.value
    return get_value

def main() -> None:
    c = Config(10)
    getter = make_getter(c)
    print(getter())
    c.value = 42
    print(getter())

main()
