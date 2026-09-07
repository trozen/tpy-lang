# A closure with an `Optional` parameter: a nested definition carries no
# pointer/movable classification for its parameters.
from tpy import Int32


def main() -> None:
    def get(x: Int32 | None) -> Int32:  # tpyc: error(/stmt\.nested_def:nesteddef\.param_type/)
        if x is not None:
            return x
        return 0

    print(get(None))


main()
