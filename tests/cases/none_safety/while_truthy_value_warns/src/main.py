from tpy import int32


def reduce_to_zero(x: int32 | None) -> int32:
    while x:  # tpyc: warning(/Truthiness check on optional value/)
        x = x - 1  # tpyc: ok
    return 0


print(reduce_to_zero(2))
print(reduce_to_zero(0))
print(reduce_to_zero(None))
