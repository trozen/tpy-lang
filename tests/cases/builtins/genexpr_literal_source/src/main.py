# A generator expression over a NON-LVALUE source (a container literal): the
# literal is a prvalue, built once in place inside the genexpr's frame (no
# move), whose iterator seeds on the first pull, unlike a bare-name source
# which the frame holds by reference.
from tpy import int32


def main() -> None:
    print(sum(x * x for x in [1, 2, 3, 4]))
    print(all(x > 0 for x in [1, 2, 3]))
    print(any(x > 5 for x in [1, 2, 3]))


main()
