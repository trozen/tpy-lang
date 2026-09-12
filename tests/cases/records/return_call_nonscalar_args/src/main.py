# A record-returning free call forwarding non-scalar args (str literal + container
# name) at both the storage-return slot (`return build(...)`) and the owned-record
# decl slot (`c = build(...)`); @nocopy Config forbids a silent copy of the return.
from tpy import int32, Own, nocopy


@nocopy
class Config:
    label: str
    size: int32

    def __init__(self, label: str, size: int32) -> None:
        self.label = label
        self.size = size


def build(label: str, values: list[int32]) -> Own[Config]:
    return Config(label, len(values))


def make() -> Own[Config]:
    xs = [10, 20, 30]
    return build("cfg", xs)


def sized() -> int32:
    ys = [1, 2]
    c = build("dec", ys)
    return c.size


def main() -> None:
    c = make()
    print(c.label)
    print(c.size)
    print(sized())


main()
