# A str/bytes view local owns its buffer once a write can reach a read of any
# straight-line or sibling-arm binding's source.
from tpy import int32

PAD = "Z" * 40


def straight_line(xs: list[str], ys: list[str]) -> None:
    v = xs[0]  # tpyc: type(str)
    v = ys[0]  # rebind to a new element source
    ys.clear()
    ys.append(PAD)
    print("straight_line", v)


def literal_then_element(ys: list[str]) -> None:
    v = "lit"  # tpyc: type(str)
    v = ys[0]  # rebind from static storage to an element
    ys.clear()
    ys.append(PAD)
    print("literal_then_element", v)


def compound_source(c: bool, xs: list[str], ys: list[str],
                    zs: list[str]) -> None:
    v = xs[0]  # tpyc: type(str)
    v = ys[0] if c else zs[0]  # rebind to a ternary over two element sources
    zs.clear()
    zs.append(PAD)
    print("compound_source", v)


def if_arms(k: int32, xs: list[str], ys: list[str]) -> None:
    if k == 1:
        v = xs[0]  # tpyc: type(str)
    else:
        v = ys[0]  # the second arm binds through the rebind path
    ys.clear()
    ys.append(PAD)
    print("if_arms", v)


def match_arms(k: int32, xs: list[str], ys: list[str]) -> None:
    match k:
        case 1:
            v = xs[0]  # tpyc: type(str)
        case _:
            v = ys[0]  # a later arm binds through the rebind path
    ys.clear()
    ys.append(PAD)
    print("match_arms", v)


def try_handler(k: int32, xs: list[str]) -> None:
    try:
        v = "lit"  # tpyc: type(str)
        if k == 1:
            raise ValueError("a")
    except ValueError:
        v = xs[0]  # the handler binds through the rebind path
    xs.clear()
    xs.append(PAD)
    print("try_handler", v)


class Holder:
    xs: list[str]
    ys: list[str]

    def __init__(self) -> None:
        self.xs = ["a" * 40]
        self.ys = ["j" * 40]

    def method(self) -> None:
        v = self.xs[0]  # tpyc: type(str)
        v = self.ys[0]  # rebind to a field container's element
        self.ys.clear()
        self.ys.append(PAD)
        print("method", v)


def bytes_face(xs: list[bytes], ys: list[bytes]) -> None:
    v = xs[0]  # tpyc: type(bytes)
    v = ys[0]  # rebind to a new element source
    ys.clear()
    ys.append(b"ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ")
    print("bytes_face", v)


def no_mutation(xs: list[str], ys: list[str]) -> None:
    v = xs[0]  # tpyc: type(StrView)
    v = ys[0]  # nothing writes either source: the local stays a view
    print("no_mutation", v)


def main() -> None:
    straight_line(["a" * 40], ["b" * 40])
    literal_then_element(["c" * 40])
    compound_source(False, ["a" * 40], ["b" * 40], ["k" * 40])
    if_arms(2, ["a" * 40], ["d" * 40])
    match_arms(2, ["a" * 40], ["e" * 40])
    try_handler(1, ["f" * 40])
    Holder().method()
    bytes_face([b"a" * 40], [b"h" * 40])
    no_mutation(["a" * 40], ["i" * 40])


main()
