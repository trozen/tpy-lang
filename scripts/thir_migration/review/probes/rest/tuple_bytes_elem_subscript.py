from tpy import int32


def get() -> tuple[int32, bytes]:
    return (0, b"ab")


def main() -> None:
    r = get()
    print(r[0], r[1])


main()
