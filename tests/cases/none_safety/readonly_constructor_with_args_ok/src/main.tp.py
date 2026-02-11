from tpy import Int32, readonly


class Box:
    @readonly
    def __init__(self, x: Int32) -> None:
        return


@readonly
def build() -> int:
    Box(3)  # tpyc: ok
    return 0


print(build())
