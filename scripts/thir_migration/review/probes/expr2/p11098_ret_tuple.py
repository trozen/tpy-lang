from tpy import int32, Own
def a1() -> Own[tuple[list[int32] | list[str], int32]]:
    return ([1], 2)
def a2() -> Own[tuple[dict[str, int32] | dict[str, str], int32]]:
    return ({"a": 1}, 2)
def a3() -> Own[tuple[list[int32] | list[str], int32]]:
    a = [1, 2]
    return ([x for x in a], 2)
def a4() -> tuple[int32 | str, int32]:
    u: int32 | str = 1
    return (u, 2)
def main() -> None:
    print(a1()[1], a2()[1], a3()[1], a4()[1])
main()
