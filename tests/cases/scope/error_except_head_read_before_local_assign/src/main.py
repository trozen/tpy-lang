# An `except` clause naming an exception class the function later rebinds to
# a value: the clause reads the function's local before it is bound
# (CPython's UnboundLocalError), never the module class.
from tpy import int32


class Oops(Exception):
    pass


def main() -> None:
    try:
        raise Oops()
    # The class read the later `Oops = 3` makes local.
    except Oops:  # tpyc: error(/'Oops' is read before it is bound on line 17: .* does not see the module's 'Oops'/)
        print("caught")
    Oops = 3
    print(Oops)


main()
