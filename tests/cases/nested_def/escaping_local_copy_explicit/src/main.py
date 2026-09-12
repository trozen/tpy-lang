# Test explicit copy() silences the copy warning and matches CPython.
# The closure captures the copy (moved at last use), while the original
# can be mutated independently -- same behavior in both TPy and CPython.
from typing import Callable
from tpy import int32, copy

class Config:
    value: int32
    def __init__(self, v: int32) -> None:
        self.value = v

def make_getter() -> Callable[[], int32]:
    cfg = Config(42)
    cfg_copy = copy(cfg)  # tpyc: ok
    def get_value() -> int32:  # tpyc: ok
        return cfg_copy.value
    cfg.value = 999
    print(cfg.value)
    return get_value

def main() -> None:
    getter = make_getter()
    print(getter())

main()
