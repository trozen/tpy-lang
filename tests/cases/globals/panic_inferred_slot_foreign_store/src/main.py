# A store from another body into a module variable its module inferred as
# uint8 narrows with the run-time check (and warns): the 256th increment
# raises OverflowError where Python stores 256.
from tpy import uint8

g = uint8(0)


def inc() -> None:
    global g
    g += 1  # tpyc: warning(/'g' was inferred as uint8 in its module; this store from 'inc' narrows int32 into it/)


def main() -> None:
    for i in range(256):
        inc()
    print(g)


main()
