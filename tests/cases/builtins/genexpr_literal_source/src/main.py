# A generator expression over a NON-LVALUE source (a container literal): the
# literal is a prvalue, so it moves into the make_generator lambda's own storage
# (`__src = std::array<...>({...})` + a `__started` seed), unlike a bare-name
# source which the outer IIFE aliases by reference.
from tpy import int32


def main() -> None:
    print(sum(x * x for x in [1, 2, 3, 4]))
    print(all(x > 0 for x in [1, 2, 3]))
    print(any(x > 5 for x in [1, 2, 3]))


main()
