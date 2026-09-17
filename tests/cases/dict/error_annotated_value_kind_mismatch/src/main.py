# A nested container literal adopts the enclosing slot's annotation only when
# it is the same KIND of container: a set literal at a `list[int32]` value
# slot is still a type mismatch, not a set silently stored as a list.
from tpy import int32


def main() -> None:
    d: dict[str, list[int32]] = {"a": {1, 2}}  # tpyc: error(/expected list\[int32\], got set\[int32\]/)
    print(len(d))


main()
