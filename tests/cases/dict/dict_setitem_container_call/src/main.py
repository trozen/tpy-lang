# A by-value CALL rvalue at a nested-CONTAINER element store, the shape a
# record element already had: `d["k"] = make()` forwards the call bare into
# the checked __setitem__, for a free call, a method call and an Array value.
# The bytearray and set legs pin the STORE only -- reading such an element
# back has no row yet, so there is nothing to observe through. The `Box` leg
# is what makes those stores checkable anyway: `Box` is @nocopy, so a copy
# into the element slot is a C++ compile error rather than a duplicate the
# output cannot show.
from tpy import Array, int32, Own
from tplib.box import Box


class Source:
    seed: int32

    def __init__(self, seed: int32) -> None:
        self.seed = seed

    def rows(self) -> Own[list[int32]]:
        return [self.seed, self.seed + 1]


def make_list() -> Own[list[int32]]:
    return [1, 2]


def make_inner() -> Own[dict[str, int32]]:
    d: dict[str, int32] = {}
    d["n"] = 5
    return d


def make_pair() -> Own[Array[int32, 2]]:
    return [8, 9]


def make_blob() -> Own[bytearray]:
    return bytearray(b"abc")


def make_tags() -> Own[set[int32]]:
    return {4, 5}


def make_boxes() -> Own[list[Box[int32]]]:
    return [Box(1), Box(2)]


def main() -> None:
    s = Source(10)
    d: dict[str, list[int32]] = {}
    d["free"] = make_list()      # tpyc: ok -- the free-call rvalue
    d["meth"] = s.rows()         # tpyc: ok -- the method-call rvalue
    # The stored value is the container the call built, not a stale copy:
    # mutating it through the map is visible on the next read.
    d["free"].append(3)
    print(len(d), len(d["free"]), d["free"][2], d["meth"][0])

    nested: dict[str, dict[str, int32]] = {}
    nested["in"] = make_inner()  # tpyc: ok -- a dict-valued slot
    nested["in"]["m"] = 6
    print(len(nested["in"]), nested["in"]["n"], nested["in"]["m"])

    pairs: dict[str, Array[int32, 2]] = {}
    pairs["p"] = make_pair()     # tpyc: ok -- an Array-valued slot
    pairs["p"][0] = 42
    print(pairs["p"][0], pairs["p"][1])

    blobs: dict[str, bytearray] = {}
    blobs["b"] = make_blob()     # tpyc: ok -- a bytearray-valued slot
    owned = bytearray(b"de")
    blobs["n"] = owned           # tpyc: ok -- the NAME source, moved at last use
    tags: dict[str, set[int32]] = {}
    tags["t"] = make_tags()      # tpyc: ok -- a set-valued slot
    boxes: dict[str, list[Box[int32]]] = {}
    boxes["b"] = make_boxes()    # tpyc: ok -- a copy here would not compile
    print(len(blobs), len(tags), len(boxes["b"]))

    rows: list[list[int32]] = [[0]]
    rows[0] = make_list()        # tpyc: ok -- a list receiver, same arm
    rows[0].append(7)
    print(len(rows[0]), rows[0][2])


main()
