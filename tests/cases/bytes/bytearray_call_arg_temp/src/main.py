# A bytearray-returning CALL passed straight into a bytearray parameter -- the
# argument lands in a temp. The named leg proves the parameter aliases the
# caller's buffer rather than copying it.
def show(b: bytearray) -> None:
    print(len(b))


def grow(b: bytearray) -> None:
    b.append(120)


def main() -> None:
    show(bytearray(b"abc"))  # rvalue call at a bytearray slot -> argument temp
    named = bytearray(b"ab")
    grow(named)
    print(len(named), named[2])


main()
