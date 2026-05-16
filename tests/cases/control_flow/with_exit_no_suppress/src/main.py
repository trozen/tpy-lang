# __exit__ returning False (or None) does NOT suppress; the exception propagates
# out of the with and is caught by an outer handler.


class Bouncer:
    def __enter__(self) -> int:
        print("enter")
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_val is not None:
            print(f"exit saw: {str(exc_val)}")
        else:
            print("exit clean")
        return False


def main() -> None:
    try:
        with Bouncer() as x:
            print(f"x={x}")
            raise ValueError("propagate me")
    except ValueError as e:
        print(f"outer caught: {str(e)}")


main()
