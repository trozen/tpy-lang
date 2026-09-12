# The adjacent shape that must keep rejecting: `Own[list[T]] | None` is
# VALUE-repr (`std::optional<std::vector<...>>`), so it never reaches the
# optional_to_ptr binding the plain `Optional[container]` field read takes --
# which is why the reference-axis gate must not peel `Own`.
from tpy import int32, Own


def first(src: Own[list[int32]] | None) -> int32:
    xs = src  # tpyc: error(/not yet supported/)
    if xs is None:
        return 0
    return len(xs)


def main() -> None:
    print(first([1, 2]))


main()
