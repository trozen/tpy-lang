# Method arguments are decided by ONE row listing for every receiver kind, so
# a shape written for a builtin-stub slot decides the same shape at a user
# record's slot -- and the other way round. Every marked line below is a shape
# that reaches its receiver kind only because the two listings are one.
from tpy import int32, Own, nocopy, readonly


@nocopy
class Tag:
    # @nocopy so a silent COPY at either stub-slot leg below is a compile
    # error rather than an invisible extra object.
    ident: int32

    def __init__(self, ident: int32) -> None:
        self.ident = ident

    def __eq__(self, other: 'Tag') -> bool:
        return self.ident == other.ident


class Sink:
    n: int

    def __init__(self) -> None:
        self.n = 0

    def store_name(self, s: Own[str]) -> None:
        self.n = len(s)

    def store_width(self, w: Own[int32]) -> None:
        self.n = int(w)

    def store_blob(self, b: Own[bytes]) -> None:
        self.n = len(b)

    def soak(self, row: readonly[list[float]]) -> None:
        self.n = len(row)


def main() -> None:
    k = Sink()
    name = "abc"
    # A str NAME at an Own[str] slot: the view->owned convert, not a move --
    # `name` still reads its own value afterwards.
    k.store_name(name)  # tpyc: ok
    print(k.n, name)

    # A bytes LITERAL at an Own[bytes] slot.
    k.store_blob(b"xyz")  # tpyc: ok
    print(k.n)

    # A bytes NAME at the same slot: the by-value vector slot takes a copy
    # (`bytes_copy`), so `blob` still reads its own value afterwards.
    blob = b"pqrs"
    k.store_blob(blob)  # tpyc: ok
    print(k.n, len(blob))

    # A comprehension at a readonly container slot: the statement-expression
    # is a prvalue and the const borrow binds it for the full expression.
    k.soak([2.0 * float(i) for i in range(4)])  # tpyc: ok
    print(k.n)

    # An f-string at an Own[str] slot, and a BigInt name at an Own[int32]
    # one: both are conversions, so the prvalue binds the by-value slot with
    # no copy temp. `wide` is spelled `int` because the row under test is the
    # BigInt narrowing (`to_fixed_check<int32_t>`), which an inferred int32
    # would never reach.
    k.store_name(f"v{k.n}")  # tpyc: ok
    print(k.n)
    wide: int = 7
    k.store_width(wide)  # tpyc: ok
    print(k.n)

    # The other direction -- shapes only a RECORD row names, at a builtin
    # stub's slot. A record NAME and a record CTOR RVALUE at `list.index` /
    # `list.remove`'s bare-T slot, which the runtime takes as `const T&`.
    tags: list[Tag] = []
    tags.append(Tag(1))
    tags.append(Tag(2))
    probe = Tag(2)
    print(tags.index(probe), probe.ident)  # tpyc: ok
    tags.remove(Tag(1))  # tpyc: ok
    print(len(tags), tags[0].ident)

    # ... and a tuple LITERAL with a last-use record element at a stub's
    # Own[tuple] slot. The element MOVES into the storage tuple, so the case
    # reads it back out of the container rather than through the moved-from
    # name.
    pairs: list[tuple[Tag, int32]] = []
    moved = Tag(5)
    pairs.append((moved, 3))  # tpyc: ok
    for held, width in pairs:
        print(len(pairs), held.ident, width)


main()
