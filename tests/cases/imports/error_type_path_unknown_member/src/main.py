# A dotted path to a class whose member names nothing is reported in the
# user's spelling, as CPython's AttributeError names the class.
import m


def main() -> None:
    m.Outer.Nope(1)  # tpyc: error(/class 'm.Outer' has no attribute 'Nope'/)


main()
