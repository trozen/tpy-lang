# Normal (non-exception) path through `with`: __exit__ sees exc_val=None.
# Also exercises early-return through the with body (finally chain emits
# __exit__({}, nullptr, {}) before the function-level return).


class Tracker:
    def __enter__(self) -> int:
        print("enter")
        return 7

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_val is None:
            print("clean exit")
        else:
            print(f"exception exit: {str(exc_val)}")


def fall_through() -> None:
    with Tracker() as x:
        print(f"body sees x={x}")
    print("after fall_through")


def early_return() -> int:
    with Tracker() as x:
        return x


def main() -> None:
    fall_through()
    print("---")
    r = early_return()
    print(f"got {r}")


main()
