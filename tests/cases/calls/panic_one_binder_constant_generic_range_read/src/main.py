# A default-int read of an integer type argument is checked: N = 3000000000
# under the int32 default raises OverflowError at the read, never wraps.


class Box[N: int]:
    def run(self) -> None:
        # The checked int32 read of N.
        print(N + 1)  # tpyc: ok


def main() -> None:
    Box[3000000000]().run()


main()
