# The auto-move of an Own[recursive-union] result into an annotated local
# applies only when the target type is compatible; an unrelated annotation
# is still a type error (the fix accepts more, it does not coerce blindly).
from tpy import Own

type Json = None | int | str | list[Json] | dict[str, Json]


def build() -> Own[Json]:
    d: dict[str, Json] = {"a": 1}
    return d


def main() -> None:
    n: int = build()  # tpyc: error(/expected int, got Json/)
    print(n)


main()
