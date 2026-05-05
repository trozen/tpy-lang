# D16 v1.5 phase 7: hasattr on a dyn-readable class -- runtime check via try/catch
# of __getattr__'s AttributeError. True when the dunder returns, False on raise.

class Headers:
    _origin: str

    def __init__(self, origin: str) -> None:
        self._origin = origin

    def __getattr__(self, name: str) -> str:
        if name == "host":
            return self._origin
        raise AttributeError(name)


def main() -> None:
    h = Headers("example.com")
    print(hasattr(h, "host"))     # True via dunder
    print(hasattr(h, "missing"))  # False via dunder raise


main()
