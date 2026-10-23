# Two generic inheritance levels, G[T] <- H[W](G[list[W]]) <- K(H[int32]):
# every lookup kind from K sees G's members at T = list[int32].
from tpy import int32


class G[T]:
    item: T
    tag: str

    def __init__(self, item: T) -> None:
        # a T that is a reference type is copied into the field here
        self.item = item  # tpyc: warning(/may copy T into field/)
        self.tag = "g"

    def get(self) -> T:
        return self.item

    def put(self, item: T) -> None:
        # the same copy as the constructor's, at a method
        self.item = item  # tpyc: warning(/may copy T into field/)

    @property
    def first(self) -> T:
        return self.item

    def describe(self) -> str:
        return "G.describe"


class H[W](G[list[W]]):
    def __init__(self, item: list[W]) -> None:
        super().__init__(item)


class Mixin:
    def describe(self) -> str:
        return "Mixin.describe"


class K(H[int32]):
    def __init__(self, item: list[int32]) -> None:
        # ctor: super().__init__ reaches G.__init__ through H at T = list[int32]
        super().__init__(item)

    def via_base(self) -> int32:
        # Base.m(self) naming the ancestor two levels up
        return len(G.get(self))


# M.describe overrides G.describe statically: a 'G' reference still calls G's
class M(K, Mixin):  # tpyc: warning(/hides 'G.describe'/)
    def describe(self) -> str:
        # super().m() in a multi-base class: MRO M, K, H, G, Mixin -> G's
        return "M+" + super().describe()

    def size(self) -> int32:
        # super() in a multi-base class reaching a generic method two levels up
        return len(super().get())


def main() -> None:
    k = K([1, 2])
    # method: inherited generic methods two levels up, read and write
    print("method:", len(k.get()), k.get()[0])
    k.put([4, 5, 6])
    print("method_put:", len(k.get()))
    print("ctor:", k.tag, len(k.item))
    print("base_call:", k.via_base())
    # field: an inherited generic field read and mutated, a plain one written
    k.item.append(7)
    k.tag = "k"
    print("field:", k.tag, len(k.item), k.item[3])
    # property: an inherited generic getter two levels up
    print("property:", len(k.first), k.first[0])
    m = M([3])
    print("super_multi:", m.describe(), m.size())
    # alias: the list returned by the inherited getter is k's own; a mutation
    # after the boundary is seen through k
    got = k.get()
    got.append(12)
    print("alias:", len(k.item), k.item[4])


main()
