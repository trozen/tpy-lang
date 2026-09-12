# Test escaping closure copying local non-value object: warns about copy.
# The local is used after the closure, so it can't be moved -- must copy.
# Mutation after closure creation is NOT visible through the closure
# (differs from CPython where the closure captures by reference).
from typing import Callable
from tpy import int32

class Config:
    value: int32
    def __init__(self, v: int32) -> None:
        self.value = v

def make_getter() -> Callable[[], int32]:
    cfg = Config(42)
    def get_value() -> int32:  # tpyc: warning(/copies local 'cfg'.*used after closure/)
        return cfg.value
    cfg.value = 999
    print(cfg.value)
    return get_value

def main() -> None:
    getter = make_getter()
    print(getter())

main()
