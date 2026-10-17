# A tuple loop variable rebound while a loop iterates one of its elements: the
# holder has no name, so the refusal names the pointer-or-iterator holder.
from tpy import Own, int32


def pair(n: int32) -> Own[tuple[list[int32], list[int32]]]:
    return ([n], [n + 1])


def iterated_element(items: list[tuple[list[int32], list[int32]]]) -> None:
    for t in items:
        for b in t[0]:
            t = pair(9)  # tpyc: error(/a pointer taken from an element of 't', or an iterator over it, still refers to the object that element was bound to/)
            print(b, t[0][0])


iterated_element([([1], [2])])
