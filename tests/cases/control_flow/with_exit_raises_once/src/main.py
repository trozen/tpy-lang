# Regression: an __exit__ that RAISES on the with's fall-through path must run
# exactly once. The fall-through __exit__ copy sits after the catches, so its
# throw propagates instead of being caught by the with's own catch-all (which
# would call __exit__ a second time). Sibling of with_exit_double_call, which
# pins the suppressing path this shape must not regress.


class Thrower:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("exit ran")
        raise RuntimeError("from exit")


class Quiet:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __enter__(self) -> int:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(f"exit {self.tag}")


def fall_through() -> None:
    try:
        with Thrower():
            print("body")
    except RuntimeError as e:
        print(f"caught: {str(e)}")


def nested_inner_throws() -> None:
    # The inner __exit__ throws on fall-through; the outer manager must still
    # see the exceptional path and run its own __exit__ exactly once.
    try:
        with Quiet("outer"):
            with Thrower():
                print("nested body")
    except RuntimeError as e:
        print(f"caught nested: {str(e)}")


def body_raises_exit_throws() -> None:
    # Body raises first: __exit__ runs on the catch path (not the fall-through
    # copy), and its own throw replaces the in-flight exception -- once.
    try:
        with Thrower():
            raise ValueError("from body")
    except RuntimeError as e:
        print(f"caught replaced: {str(e)}")


def main() -> None:
    fall_through()
    nested_inner_throws()
    body_raises_exit_throws()


main()
