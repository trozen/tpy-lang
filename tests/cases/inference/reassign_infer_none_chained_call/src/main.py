from tpy import int32, Own


class Product:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value


class Factory:
    def create(self) -> Own[Product]:
        return Product(int32(9))


def get_factory() -> Own[Factory]:
    return Factory()


x = None
x = get_factory().create()
print(x.value)
