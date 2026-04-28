# Final[tuple[...]] class constant: emit as static constexpr std::tuple<...>.
from typing import Final
from tpy import Int32


class Version:
    SEMVER: Final[tuple[Int32, Int32, Int32]] = (1, 2, 3)
    LABEL: Final[tuple[str, bool]] = ("alpha", True)


def main() -> None:
    major, minor, patch = Version.SEMVER
    print(major)
    print(minor)
    print(patch)
    name, stable = Version.LABEL
    print(name)
    print(stable)


main()
