# A dict LITERAL key that comes from a BigInt-returning call takes the checked
# narrow to the declared key width, like the list-element position
# (int/panic_int_to_fixed_aggregate_overflow) and the `d[k] = v` store
# (panic_dict_int64_key_overflow): out of range panics before the key is hashed.
from tpy import int32


def too_big() -> int:
    return 10_000_000_000  # > int32 max


def main() -> None:
    d: dict[int32, str] = {too_big(): "boom"}
    print(len(d))


main()
