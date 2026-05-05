# getattr's default must be coercible to __getattr__'s return type.
# Here __getattr__ returns str but the default is an int.

class Headers:
    def __getattr__(self, name: str) -> str:
        raise AttributeError(name)


def main() -> None:
    h = Headers()
    print(getattr(h, "x", 42))  # tpyc: error(/getattr.*default.*expected/)


main()
