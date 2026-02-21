from tpy import Int32, Own


class Product:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value


class Factory:
    def create(self) -> Own[Product]:
        return Product(Int32(9))


def get_factory() -> Own[Factory]:
    return Factory()


x = None
x = get_factory().create()
print(x.value)
