# A closure with an `Optional` parameter: a nested definition carries no
# pointer/movable classification for its parameters.
from tpy import int32


def main() -> None:
    def get(x: int32 | None) -> int32:  # tpyc: error(/stmt\.nested_def:nesteddef\.param_type/)
        if x is not None:
            return x
        return 0

    print(get(None))


main()
