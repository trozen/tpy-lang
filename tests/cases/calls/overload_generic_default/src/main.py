# @overload copies whose default names a type parameter (`T()`) keep it as a
# C++ default; a local a call takes by address is no literal there.
from typing import Literal, overload
from tpy import Ptr, int32, take_ptr
from tpy.extern import cpp_template, value_ptr_coercion
from tpy.unsafe import unsafe_store


@overload
def pick[T](x: T, y: T = T()) -> T: ...
@overload
def pick[T](x: T, y: T, z: int32) -> T: ...
def pick[T](x: T, y: T = T(), z: int32 = 0) -> T:  # tpyc: ok
    if z > 0:
        return x
    return y


# posonly: a later parameter's default ranks the call, `T()` stays C++'s
@overload
def posonly[T](x: T, y: T = T(), z: int32 = 5, /) -> T: ...
@overload
def posonly[T](x: T, y: T, z: str, /) -> T: ...
def posonly[T](x: T, y: T = T(), z: int32 | str = 5, /) -> T:  # tpyc: ok
    print("posonly z", z)
    return y


# class type parameter: the method copy's `T()` default is its own
class Bin[T]:
    @overload
    def take(self, x: int32, y: T = T()) -> T: ...
    @overload
    def take(self, x: str, y: T = T()) -> T: ...
    def take(self, x: int32 | str, y: T = T()) -> T:  # tpyc: ok
        if isinstance(x, str):
            return y
        return y


@overload
def lit0(x: Literal[0]) -> str: ...
@overload
def lit0(x: int32) -> str: ...
def lit0(x: int32) -> str:
    if x == 0:
        return "zero"
    return "impl sees " + str(x)


def poke(p: Ptr[int32]) -> None:
    unsafe_store(p, 0, 7)


@value_ptr_coercion
@cpp_template("(*{0} = 7)")
def bump(p: Ptr[int32], mode: str) -> int32: ...


@value_ptr_coercion
@cpp_template("(*({0})[0] = 7)")
def vbump(*ps: Ptr[int32]) -> int32: ...


def require_r(mode: Literal["r"]) -> str:
    return mode


def address_taken() -> None:
    # written through the pointer, so no literal proves rc's value
    rc = 0
    poke(take_ptr(rc))
    print("address taken", rc, lit0(rc))  # tpyc: ok
    # nor is the test of it decided
    if rc == 0:  # tpyc: ok
        print("address taken arm", "zero")
    else:
        print("address taken arm", rc)
    # only the argument at the Ptr parameter is taken by address
    n = 0
    s = "r"
    bump(n, s)
    print("non-ptr argument", n, require_r(s))  # tpyc: ok
    # an element of a `*args: Ptr` pack is taken by address
    v = 0
    vbump(v)
    if v == 0:  # tpyc: ok
        print("vararg ptr", "zero")
    else:
        print("vararg ptr", v)
    # so is a keyword argument at the Ptr parameter, and only that one
    k = 0
    t = "r"
    bump(p=k, mode=t)
    print("keyword ptr", k, lit0(k), require_r(t))  # tpyc: ok


def main() -> None:
    print("inferred", pick(3), pick("a"))  # tpyc: ok
    print("explicit", pick[int32](3), pick(3, 4), pick(3, 4, 1))  # tpyc: ok
    a = posonly(3)  # tpyc: ok
    b = posonly(3, 4)
    print("posonly", a, b)
    print("class", Bin[int32]().take(1), Bin[str]().take("a", "b"))  # tpyc: ok
    address_taken()


main()
