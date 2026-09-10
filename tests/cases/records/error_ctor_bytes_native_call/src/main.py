# `bytes(x)` at a member-init field is a CALL the member-init slice does not
# spell. (The case name says `native_call` for the tag this rejected under
# while the buffer constructors named a factory; the owned type constructs from
# its own view now, so the same shape rejects one tag over.)
class Blob:
    data: bytes

    def __init__(self, src: bytes) -> None:
        self.data = bytes(src)  # tpyc: error(/ctor.mil_field.nominal.call/)


def main() -> None:
    print(len(Blob(b"ab").data))


main()
