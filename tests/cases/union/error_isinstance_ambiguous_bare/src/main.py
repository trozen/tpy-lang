# A bare-generic isinstance against a union with two members of the same
# container kind is ambiguous: CPython cannot distinguish list[int] from
# list[str] at runtime either (type erasure), so there is no member to
# narrow to. tpyc rejects rather than guessing.
def f(x: list[int] | list[str]) -> None:
    if isinstance(x, list):  # tpyc: error(/ambiguous/)
        print("list")


def main() -> None:
    f([1, 2])


main()
