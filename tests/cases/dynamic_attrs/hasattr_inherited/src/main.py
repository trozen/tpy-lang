# hasattr / 3-arg getattr work via inherited __getattr__: child class has no
# dunder of its own; lookup walks MRO to the parent.


class Bag:
    declared: str

    def __init__(self, declared: str) -> None:
        self.declared = declared

    def __getattr__(self, name: str) -> str:
        if name == "host":
            return "example.com"
        raise AttributeError(name)


class Child(Bag):
    def __init__(self) -> None:
        super().__init__("d")


def main() -> None:
    c = Child()
    print(hasattr(c, "declared"))   # True (declared on parent, compile-time)
    print(hasattr(c, "host"))       # True (inherited dunder finds it)
    print(hasattr(c, "missing"))    # False (inherited dunder raises)
    print(getattr(c, "host", "fallback"))     # "example.com" via inherited dunder
    print(getattr(c, "missing", "fallback"))  # "fallback" via inherited raise


main()
