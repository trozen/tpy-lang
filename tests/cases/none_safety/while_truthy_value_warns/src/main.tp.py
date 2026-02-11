from tpy import Int32


def reduce_to_zero(x: Int32 | None) -> Int32:
    while x:  # tpyc: warning(/Truthiness check on optional value/)
        x = x - 1  # tpyc: ok
    return 0


print(reduce_to_zero(2))
print(reduce_to_zero(0))
print(reduce_to_zero(None))
