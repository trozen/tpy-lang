from tpy import Int32, Own
def a1() -> Own[tuple[list[Int32] | list[str], Int32]]:
    return ([1], 2)
def a2() -> Own[tuple[dict[str, Int32] | dict[str, str], Int32]]:
    return ({"a": 1}, 2)
def a3() -> Own[tuple[list[Int32] | list[str], Int32]]:
    a = [1, 2]
    return ([x for x in a], 2)
def a4() -> tuple[Int32 | str, Int32]:
    u: Int32 | str = 1
    return (u, 2)
def main() -> None:
    print(a1()[1], a2()[1], a3()[1], a4()[1])
main()
