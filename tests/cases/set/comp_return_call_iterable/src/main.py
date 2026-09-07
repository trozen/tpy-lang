# A set comprehension in RETURN position over a container-returning CALL
# iterable -- the free-call arm of the comprehension route.
from tpy import Int32, Own


def make() -> Own[list[Int32]]:
    return [1, 2, 2]


def uniq() -> Own[set[Int32]]:
    return {x for x in make()}  # call iterable at a return-position comp


def main() -> None:
    print(len(uniq()))


main()
