# A set comprehension in RETURN position over a container-returning CALL
# iterable -- the free-call arm of the comprehension route.
from tpy import int32, Own


def make() -> Own[list[int32]]:
    return [1, 2, 2]


def uniq() -> Own[set[int32]]:
    return {x for x in make()}  # call iterable at a return-position comp


def main() -> None:
    print(len(uniq()))


main()
