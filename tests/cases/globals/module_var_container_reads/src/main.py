# A container GLOBAL of another module, read as `mod.X` at the positions a
# caller hits first. The read is a pointer slot, so each consumer has to
# accept the `(*slot)` deref: the element template, the printer wrap, and a
# by-reference parameter.
import sys
import tables


def add_name(xs: list[str], v: str) -> None:
    xs.append(v)


def main() -> None:
    # Subscript receiver.
    print(tables.NAMES[0], tables.NAMES[1])
    # Print argument (the whole container).
    print(tables.NAMES)
    # Call argument at a plain `list[str]&` slot -- passed by reference, so
    # the mutation below is visible through the module global afterwards.
    add_name(tables.NAMES, "gamma")
    print(tables.NAMES)
    # The same two shapes over a STDLIB pointer-slot global. Output stays
    # host-independent: argv[0] is a path, so only its emptiness is printed.
    print(len(sys.argv) >= 1, len(sys.argv[0]) > 0)


main()
