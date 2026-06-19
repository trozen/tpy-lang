# A scalar-member inline union as the isinstance second arg (sibling of the
# concrete-class case). All-value union, so no pointer-variant interaction.
def kind(v: int | float | str) -> str:
    if isinstance(v, int | float):
        return "num"
    return "str"


def main() -> None:
    a: int | float | str = 5
    print(kind(a))
    b: int | float | str = "hi"
    print(kind(b))


main()
