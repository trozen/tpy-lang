# A name the function binds only by `del`: the `del` makes it local, so it is
# CPython's UnboundLocalError, never a deletion of the module global. No line
# names a binding: the `del` is the only one.
from tpy import int32

G = 7


def main() -> None:
    # The `del` the name's only binding makes local.
    del G  # tpyc: error(/'G' is deleted before it is bound: a name bound anywhere in a function is local to the whole function, so this deletion does not see the module's 'G'/)


main()
