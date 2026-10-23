# A container literal as a list index is the key's type mismatch, naming
# every type the index takes, as any other key of the wrong type is.
from tpy import int32


def main() -> None:
    xs: list[int32] = [1]
    print(xs[[]])  # tpyc: error(/Type mismatch in list index: expected int32 or slice, got list\[\?\?\?\]/)


main()
