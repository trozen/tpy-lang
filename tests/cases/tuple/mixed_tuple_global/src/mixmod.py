from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


ilender = Box(30)
# Defined here, read from main: the importing module takes the layout off the binding.
imixed = make_mixed(ilender)
