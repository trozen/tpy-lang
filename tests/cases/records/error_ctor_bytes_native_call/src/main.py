# `bytes(x)` resolves to the @native(function=True) __init__ overload, an emit
# the member-init call slice does not spell.
class Blob:
    data: bytes

    def __init__(self, src: bytes) -> None:
        self.data = bytes(src)  # tpyc: error(/ctor.mil_field.nominal.native_call/)


def main() -> None:
    print(len(Blob(b"ab").data))


main()
