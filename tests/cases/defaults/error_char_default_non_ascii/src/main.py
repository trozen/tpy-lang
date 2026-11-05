# A non-ASCII one-character literal does not fit one C++ char, so a char
# parameter's default refuses it (an ASCII one initializes it:
# defaults/char_defaults).
from tpy import char


def g(x: char = "é") -> str:  # tpyc: error(/Type mismatch in parameter 'x': expected char, got str/)
    return "g " + str(x)


def main() -> None:
    print(g())


main()
