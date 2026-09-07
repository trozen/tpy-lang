# `isinstance` narrowing of an `Any` value in its three condition forms: a
# chain, a tuple of types, and a NEGATED test.
from typing import Any
from tpy import Int32


def dispatch(v: Any) -> None:
    if isinstance(v, int):
        print("int", v)
    elif isinstance(v, str):
        print("str", v)
    else:
        print("other")


def tuple_form(v: Any) -> bool:
    if isinstance(v, (int, float)):
        return True
    return False


def negated(v: Any) -> bool:
    # The negated test lowers to the inverted check.
    if not isinstance(v, str):
        return False
    return True


def main() -> None:
    dispatch(1)
    dispatch("s")
    dispatch(1.5)
    print(tuple_form(1))
    print(tuple_form("s"))
    print(negated("s"))
    print(negated(1))


main()
