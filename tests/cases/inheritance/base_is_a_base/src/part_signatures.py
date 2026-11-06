# Inherited signatures read at the subclass's binding: generic methods of a
# non-generic base, staticmethods, an awaited Future subclass, sorted().
import asyncio
from asyncio import Future
from tpy import int32
from part_init import DBare, LBare


class ChooseBase:
    def choose[U](self, value: U) -> U:
        return value


class ChooseChild[U](ChooseBase):
    pass


# a generic method of a non-generic base keeps its own type parameter under a
# subclass parameter of the same name
def nongeneric_base_method() -> None:
    print("nongeneric-base", ChooseChild[int32]().choose(1.5))  # tpyc: ok


class StaticG[T]:
    @staticmethod
    def ident(x: T) -> T:
        return x

    @staticmethod
    def pick[U](x: U) -> U:
        return x


class StaticPlain:
    @staticmethod
    def pick[U](x: U) -> U:
        return x


class StaticPlainSub[U](StaticPlain):
    pass


class StaticConcSub[U](StaticG[int32]):
    pass


class FutSub[U](Future[U]):
    pass


class FutConc(Future[int32]):
    pass


async def awaited_poll() -> None:
    f = FutSub[int32]()
    f.set_result(7)
    g = FutConc()
    g.set_result(8)
    # `await` reads the __poll__ the subclass inherits, at its binding.
    print("inherited-await", await f, await g)  # tpyc: ok


class StaticSub(StaticG[int32]):
    pass


class StaticGS[U](StaticG[U]):
    pass


def inherited_signatures() -> None:
    dd = DBare()
    dd["b"] = 1
    dd["a"] = 2
    ll = LBare()
    ll.append(3)
    ll.append(1)
    # A protocol bound's type argument read off the inherited dict / list methods.
    ks = sorted(dd)  # tpyc: ok
    es = sorted(ll)  # tpyc: ok
    dd["c"] = 3
    ll.append(0)
    print("inherited-sorted", ks, es)
    for e in reversed(ll):  # tpyc: ok
        print("inherited-reversed", e)
    names = list(dd)  # tpyc: ok
    names.append("z")
    print("inherited-list", names, len(dd))
    # An inherited staticmethod reads its signature at the subclass's binding.
    print("inherited-static", StaticSub.ident(4) + 1)  # tpyc: ok
    print("inherited-static-generic", StaticGS[int32].ident(6) + 1)  # tpyc: ok
    # The method's own type parameter is not captured by the subclass's.
    print("inherited-static-own", StaticGS[int32].pick(1.5),  # tpyc: ok
          StaticConcSub[int32].pick(2.5), StaticPlainSub[int32].pick(3.5))
    asyncio.run(awaited_poll())


def main() -> None:
    nongeneric_base_method()
    inherited_signatures()
