# A method call on a container element of a popped tuple is still refused
# (BUGS.md#tuple-container-element-method-rejects); unpacking it compiles.
from tpy import int32


def main() -> None:
    stack: list[tuple[list[str], int32]] = [(["x"], 1)]
    t = stack.pop()
    # The tuple element as a method receiver.
    t[0].append("y")  # tpyc: error(/not yet supported/)
    print(t[1])


main()
