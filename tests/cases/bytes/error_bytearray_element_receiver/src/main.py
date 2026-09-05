# The adjacent receiver SHAPE that keeps rejecting: a bytearray read out of a
# container element. Joining the container receiver family widens which TYPES
# the family claims, not which receiver expressions it can render.
def f(bas: list[bytearray]) -> None:
    bas[0].append(67)  # tpyc: error(/method.recv.subscript/)


def main() -> None:
    print(1)


main()
