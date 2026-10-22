# Refuses valid Python by the rule "Numeric widening across reassignments"
# (docs/LANGUAGE_FEATURES.md): a nested def is analyzed where it is defined,
# so a local it declares `nonlocal` takes its type there, and a wider store
# the enclosing function makes after the def is refused naming that pin.
from tpy import uint8, int64


def outer() -> None:
    k = uint8(1)

    def show() -> None:
        nonlocal k
        print(k)

    show()
    k = int64(5000000000)  # tpyc: error(/'k' was used as uint8 at line 11 \(declared nonlocal in the nested function 'show'\).*annotate its first binding/)
    print(k)


outer()
