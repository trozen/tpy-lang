from tpy import readonly


class Token:
    pass


@readonly
def bad() -> int:
    Token()  # tpyc: error(/Call to non-readonly or unknown-effect function 'Token' is not allowed in @readonly function/)
    return 0


print(0)
