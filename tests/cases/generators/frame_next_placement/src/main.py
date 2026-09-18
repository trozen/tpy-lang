# A small generator's __next__ is inline in <mod>_inl.hpp, which every consumer
# .cpp includes; a large one stays out-of-line in its own .cpp.
from gens import Bag, Cell, squares, guarded, echoed
from cyc_a import countdown
from cyc_b import total_countdown
from relay import stream


def main() -> None:
    # free function, consumed across modules
    print("free", sum(squares(5, [2])))  # tpyc: ok

    # method generator: a yielded Cell aliases the bag's element
    bag = Bag()
    for i in range(4):
        bag.cells.append(Cell(i))
    for c in bag.evens():  # tpyc: ok
        c.v += 100
    print("method", [c.v for c in bag.cells])

    # finally helper runs when the loop drains
    log: list[str] = []
    got = list(guarded([1, 2], log))  # tpyc: ok
    print("finally", got, log)

    # a comment quoting the C++ declarator does not take the inline prefix
    print("echoed", list(echoed(3)))  # tpyc: ok

    # import-cycle module
    down = list(countdown(3))  # tpyc: ok
    print("cycle", down, total_countdown(2))

    # the generator's module is reached only through relay's return type
    relayed = list(stream(4))  # tpyc: ok
    print("relayed", relayed)


main()
