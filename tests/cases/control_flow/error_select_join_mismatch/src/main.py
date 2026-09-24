# A ternary whose arms share no type names an empty literal arm by its
# container kind, never by an internal placeholder.
from tpy import int32


def main(c: bool) -> None:
    d: dict[str, int32] = {}
    # An empty list arm cannot take the dict operand's type.
    x = d if c else []  # tpyc: error(/Incompatible types in ternary expression: 'dict\[str, int32\]' and 'list'$/)
    print(len(x))


main(True)
