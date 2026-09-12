# tpy: ext_module
# CPython extension example over the free-function surface: a no-arg int64
# return (METH_NOARGS), int64 arguments (METH_VARARGS), and int/BigInt
# marshalling including values beyond int64 (hex round-trip both ways).
from tpy import int64
from tpy.extern import export


@export
def answer() -> int64:
    return 42


@export
def add(a: int64, b: int64) -> int64:
    return a + b


@export
def big_square(x: int) -> int:
    return x * x


@export
def negate(x: int) -> int:
    return -x
