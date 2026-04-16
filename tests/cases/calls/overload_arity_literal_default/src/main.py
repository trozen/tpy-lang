# Non-union impl param with a literal default narrows in the short stub so
# dead-branch elim can fold the `if count == 0` check in that specialization.
from typing import overload


@overload
def repeat(s: str) -> str: ...  # tpyc: ok

@overload
def repeat(s: str, count: int) -> str: ...  # tpyc: ok

def repeat(s: str, count: int = 0) -> str:
    if count == 0:
        return s
    result = ""
    i = 0
    while i < count:
        result = result + s
        i = i + 1
    return result


def main() -> None:
    print(repeat("ab"))
    print(repeat("ab", 3))


main()
