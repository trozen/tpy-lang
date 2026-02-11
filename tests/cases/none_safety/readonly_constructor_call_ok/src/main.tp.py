from tpy import readonly


class Token:
    @readonly
    def __init__(self) -> None:
        return


@readonly
def build_flag() -> int:
    Token()  # tpyc: ok
    return 0


print(build_flag())
