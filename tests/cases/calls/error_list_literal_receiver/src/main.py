# A list-literal method receiver: the adjacent non-name receiver kind that
# has no admitted render (the str-valued literal / f-string / concat
# receivers do), so it must keep rejecting rather than fall through.


def main() -> None:
    # No admitted row renders a container-literal receiver.
    print([1, 2, 3].index(2))  # tpyc: error(/method.recv.other/)


main()
