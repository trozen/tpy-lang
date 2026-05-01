# Yielding a StrView of a per-iteration temporary (the `name + "!"` result
# is destroyed at the end of the yield expression) leaves the consumer with
# a dangling view. Same dangle class as the lambda return check, applied
# to generator yield slots.
from typing import Iterator
from tpy import StrView

def gen() -> Iterator[StrView]:
    name = "longer than SSO buffer to defeat short-string optimization"
    yield StrView(name + "!")  # tpyc: error(/dangling|StrView|local or temporary/)

def main() -> None:
    for s in gen():
        print(s)

main()
