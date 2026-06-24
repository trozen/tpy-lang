# tpy: ext_module
# CPython extension example over the free-function surface: a no-arg Int64
# return (METH_NOARGS), Int64 arguments (METH_VARARGS), and int/BigInt
# marshalling including values beyond int64 (hex round-trip both ways).
from tpy import Int64
from tpy.extern import export


@export
def answer() -> Int64:
    return 42


@export
def add(a: Int64, b: Int64) -> Int64:
    return a + b


@export
def big_square(x: int) -> int:
    return x * x


@export
def negate(x: int) -> int:
    return -x
