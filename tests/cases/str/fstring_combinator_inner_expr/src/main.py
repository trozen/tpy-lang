# A container-constructor VALUE result interpolated into an f-string: the
# instantiation plus the map lowering compose, so the shape routes.
from tpy import Int32


def show(xs: list[Int32]) -> str:
    return f"{list(map(lambda v: v + 1, xs))}"


def main() -> None:
    print(show([1, 2]))


main()
