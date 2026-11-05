# A non-ASCII one-character literal does not fit one C++ char, so a single
# callee's char parameter refuses it (an ASCII one initializes it).
from tpy import char


def g(x: char) -> str:
    return "g " + str(x)


def main() -> None:
    print(g("é"))  # tpyc: error(/Type mismatch in argument 'x': expected char, got str/)


main()
