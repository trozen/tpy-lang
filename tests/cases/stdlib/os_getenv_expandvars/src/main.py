# os.getenv overloads (str|None vs str) + os.path.expandvars. Expansion is
# checked against getenv so the machine-specific value cancels (PATH is set in
# any environment that runs the toolchain); non-expanding cases are
# machine-independent. Byte-compared against CPython.
import os
from os.path import expandvars


def main():
    # getenv overloads: 1-arg is str|None, 2-arg-with-str-default narrows to str
    print(os.getenv("TPY_UNSET_VAR") is None)
    print(os.getenv("TPY_UNSET_VAR", "dflt"))
    print(len(os.getenv("TPY_UNSET_VAR", "")) == 0)   # len() needs the str overload
    print(os.getenv("PATH") is not None)

    # expandvars expansion: compare to getenv so the value cancels out
    print(expandvars("$PATH") == os.getenv("PATH"))
    print(expandvars("${PATH}") == os.getenv("PATH"))
    print(expandvars("p=$PATH;") == "p=" + os.getenv("PATH", "") + ";")

    # non-expanding cases (machine-independent): unset left verbatim, bare $,
    # plain text, unclosed brace
    print(expandvars("$TPY_UNSET_VAR/x"))
    print(expandvars("${TPY_UNSET_VAR}"))
    print(expandvars("cost: $ 5"))
    print(expandvars("no vars"))
    print(expandvars("${unclosed"))


main()
