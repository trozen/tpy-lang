from tpy import Int32


def ret_i32() -> Int32:
    return Int32(7)


def main() -> None:
    x = 0
    x += Int32(5)  # tpyc: warning(/does not narrow 'x' from int to Int32/)
    print(x)

    y = 0
    y += ret_i32()  # tpyc: warning(/does not narrow 'y' from int to Int32/)
    print(y)

    z: int = 0
    z += Int32(5)  # tpyc: ok
    print(z)

    w = int(0)
    w += Int32(5)  # tpyc: ok
    print(w)


main()
