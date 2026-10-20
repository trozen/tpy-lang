# An augmented subscript through a call receiver returning a builtin
# container is not lowered yet (BUGS.md#aug-container-call-receiver-rejects);
# the record twin `pick(t)[k] += 1` compiles.
from tpy import int32


def recv(xs: list[int32]) -> list[int32]:
    return xs


def main() -> None:
    xs = [1, 2]
    recv(xs)[0] += 1  # tpyc: error(/not yet supported/)
    print(xs)


main()
