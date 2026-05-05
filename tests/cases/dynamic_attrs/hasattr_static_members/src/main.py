# D16 v1.5 phase 7: hasattr resolves to compile-time True for declared
# fields / properties / methods / class constants. Each True below is folded
# at sema time, no runtime check emitted.

class Box:
    KIND: str = "box"
    width: int

    def __init__(self, width: int) -> None:
        self.width = width

    @property
    def doubled(self) -> int:
        return self.width * 2

    def label(self) -> str:
        return "Box"

    def __getattr__(self, name: str) -> str:
        raise AttributeError(name)


def main() -> None:
    b = Box(5)
    print(hasattr(b, "width"))    # declared field -> compile-time True
    print(hasattr(b, "doubled"))  # property -> compile-time True
    print(hasattr(b, "label"))    # method -> compile-time True
    print(hasattr(b, "KIND"))     # class constant -> compile-time True
    print(hasattr(b, "zzz"))      # not declared, dunder raises -> False at runtime


main()
