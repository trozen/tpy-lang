# Test expression callees: calling results of expressions, not just named functions.
from typing import Callable
from tpy import int32

def make_adder(n: int32) -> Callable[[int32], int32]:
    def add(x: int32) -> int32:
        return x + n
    return add

def make_negator() -> Callable[[int32], int32]:
    def negate(x: int32) -> int32:
        return -x
    return negate

def main() -> None:
    # Chained call: function_call()(args)
    result = make_adder(10)(5)
    print(result)

    # Store and chain
    fns: list[Callable[[int32], int32]] = [make_adder(1), make_adder(2), make_negator()]

    # Subscript call via local variable: list[index](args)
    # (fns is resolved as a name, fns[i] is a subscript expression)
    print(fns[0](100))
    print(fns[1](100))
    print(fns[2](100))

    # Uppercase variable name -- must not be confused with generic type call
    Handlers: list[Callable[[int32], int32]] = [make_adder(100)]
    print(Handlers[0](5))

main()
