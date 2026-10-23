# An augmented subscript through a call receiver returning a TypedDict is not
# lowered yet (BUGS.md#aug-container-call-receiver-rejects); a call returning
# a builtin container or a record is held and compiles.
from typing import TypedDict
from tpy import int32


class User(TypedDict):
    age: int32


def mk(u: User) -> User:
    return u


def main() -> None:
    u = User(age=1)
    mk(u)["age"] += 1  # tpyc: error(/not yet supported/)
    print(u["age"])


main()
