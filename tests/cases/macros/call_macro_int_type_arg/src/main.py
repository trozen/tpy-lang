# Regression: a MacroArg whose inferred type is a generic with an `N: int`
# param (Box[int32, 8]) must expose the int binding through TypeInfo.type_args
# as a plain int -- TypeInfo.from_tpy_type used to crash on it
# ("'int' object has no attribute 'is_value_type'").
from intargmac import describe
from tpy import int32


class Box[T, N: int]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []


def main() -> None:
    b = Box[int32, 8]()
    print(describe(b))


main()
