# Final[tuple[...]] class constant: emit as static constexpr std::tuple<...>.
# Beyond the unpack source, the constant is also bound WHOLE (`v =
# Version.SEMVER`) and subscripted in place (`Version.SEMVER[0]`); both read the
# bare qualified static, from a free function, a method and a nested-tuple
# constant alike.
from typing import Final
from tpy import int32


class Version:
    SEMVER: Final[tuple[int32, int32, int32]] = (1, 2, 3)
    LABEL: Final[tuple[str, bool]] = ("alpha", True)
    NESTED: Final[tuple[tuple[int32, int32], str]] = ((4, 5), "beta")


# free function: the constant bound whole into a local tuple slot.
def whole() -> None:
    v = Version.SEMVER  # tpyc: ok
    print("whole", v[0], v[1], v[2])


# free function: the element read in place, no intermediate binding.
def direct() -> None:
    print("direct", Version.SEMVER[0])  # tpyc: ok


# free function: a nested-tuple constant read in place.
def nested() -> None:
    print("nested", Version.NESTED[0][1])  # tpyc: ok


class Reader:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    # method: the same whole bind inside a record method body.
    def show(self) -> None:
        v = Version.SEMVER  # tpyc: ok
        print("method", self.tag, v[1])


def main() -> None:
    major, minor, patch = Version.SEMVER
    print(major)
    print(minor)
    print(patch)
    name, stable = Version.LABEL
    print(name)
    print(stable)
    whole()
    direct()
    nested()
    Reader("r").show()


main()
