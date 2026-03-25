# Test escaping closure capturing Own[T] param: must move (param dies on return)
from typing import Callable
from tpy import Int32, Own

class Config:
    value: Int32
    def __init__(self, v: Int32) -> None:
        self.value = v

def make_getter(cfg: Own[Config]) -> Callable[[], Int32]:
    def get_value() -> Int32:  # tpyc: ok
        return cfg.value
    return get_value

def main() -> None:
    c = Config(42)
    getter = make_getter(c)
    print(getter())

main()
