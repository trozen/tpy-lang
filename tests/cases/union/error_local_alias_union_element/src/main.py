# A container member whose ELEMENT is a module-LOCAL plain union alias fails the
# spelling-equal type-arg recursion, keeping the outer union out.
from tpy import Int32, StrView


type Num = Int32 | StrView


def show(d: dict[str, list[Num] | str]) -> None:
    v = d["k"]  # tpyc: error(/subscript.elem.union/)
    if isinstance(v, str):
        print(v)


def main() -> None:
    d: dict[str, list[Num] | str] = {"k": "x"}
    show(d)


main()
