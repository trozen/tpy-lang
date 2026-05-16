# __exit__ returning True suppresses an exception raised in the body
# (v1.5 M1: binary suppression -- the bool gates the rethrow).


class Suppressor:
    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> str:
        print(f"enter {self.name}")
        return self.name

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_val is not None:
            print(f"{self.name} suppressing: {str(exc_val)}")
            return True
        print(f"{self.name} normal exit")
        return False


def raises_inside() -> None:
    with Suppressor("S") as s:
        print(f"using {s}")
        raise ValueError("boom")
    print("control reached post-with (suppressed)")


def normal_inside() -> None:
    with Suppressor("S") as s:
        print(f"using {s}")
    print("post-with")


def main() -> None:
    raises_inside()
    print("---")
    normal_inside()


main()
