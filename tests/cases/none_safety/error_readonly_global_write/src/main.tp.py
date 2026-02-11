from tpy import Int32, readonly


x: Int32 = 0


@readonly
def bad() -> None:
    x = 1  # tpyc: error(/Cannot assign to global 'x' inside @readonly function/)
