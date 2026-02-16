from tpy import readonly


class Token:
    pass


@readonly
def build_flag() -> int:
    Token()  # tpyc: ok
    return 0


print(build_flag())
