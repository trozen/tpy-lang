# Star imports from a cycle peer still being imported, which CPython's copy
# lacks a name of for a reason other than a plain late def: `__all__` bound
# or extended late, a builtin's name, a name the peer imports late.
from datetime import date
import lpa
import aua
import bsa
import sra
import lpb
import aub
import bsb
import srb


def main() -> None:
    print("late __all__:", lpb.f())
    print("late update:", aub.f(aua.F()))
    print("shadowed builtin:", bsb.f())
    print("late import:", srb.f(date(2026, 11, 17)))


main()
