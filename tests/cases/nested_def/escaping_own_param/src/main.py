# Test escaping closure capturing Own[T] param: must move (param dies on return)
from typing import Callable
from tpy import int32, Own, ValueType

class Config:
    value: int32
    def __init__(self, v: int32) -> None:
        self.value = v

class Pt(ValueType):
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def make_getter(cfg: Own[Config]) -> Callable[[], int32]:
    def get_value() -> int32:  # tpyc: ok
        return cfg.value
    return get_value

def apply(f: Callable[[int32], int32], v: int32) -> int32:
    return f(v)

# lambda position, by-value (escaping) capture of an `Own[T]` param whose
# PAYLOAD C++ can copy: the wrapper is peeled before the copyability verdict,
# so this keeps its `[p]` entry. The peel is what error_lambda_nocopy_snapshot
# pins from the other side.
def own_value(p: Own[Pt]) -> int32:  # tpyc: ok
    return apply(lambda i: i + p.x, 1)

def main() -> None:
    c = Config(42)
    getter = make_getter(c)
    print(getter())
    print("own_value", own_value(Pt(10)))

main()
