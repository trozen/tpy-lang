# Test expression callees: calling results of expressions, not just named functions.
from typing import Callable
from tpy import Int32

def make_adder(n: Int32) -> Callable[[Int32], Int32]:
    def add(x: Int32) -> Int32:
        return x + n
    return add

def make_negator() -> Callable[[Int32], Int32]:
    def negate(x: Int32) -> Int32:
        return -x
    return negate

def main() -> None:
    # Chained call: function_call()(args)
    result = make_adder(10)(5)
    print(result)

    # Store and chain
    fns: list[Callable[[Int32], Int32]] = [make_adder(1), make_adder(2), make_negator()]

    # Subscript call via local variable: list[index](args)
    # (fns is resolved as a name, fns[i] is a subscript expression)
    print(fns[0](100))
    print(fns[1](100))
    print(fns[2](100))

main()
