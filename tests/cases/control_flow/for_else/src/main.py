# for/else: else block runs when loop completes without break
from tpy import int32

def search_break(items: list[int32], target: int32) -> None:
    for item in items:
        if item == target:
            print("found")
            break
    else:
        print("not found")

def no_break() -> None:
    for i in range(int32(3)):
        print(i)
    else:
        print("complete")

def with_continue() -> None:
    items: list[int32] = [int32(1), int32(2), int32(3)]
    for x in items:
        if x == int32(2):
            continue
        print(x)
    else:
        print("done")

def nested_inner_else() -> None:
    for i in range(int32(3)):
        for j in range(int32(3)):
            if j == int32(1):
                break
        else:
            print("inner complete")
        print(i)

def nested_outer_else() -> None:
    """Inner break must not affect outer else."""
    for i in range(int32(3)):
        for j in range(int32(3)):
            if j == int32(1):
                break
        print(i)
    else:
        print("outer complete")

def nested_both_else() -> None:
    """Both inner and outer have else; inner always breaks."""
    for i in range(int32(3)):
        for j in range(int32(3)):
            if j == int32(1):
                break
        else:
            print("inner complete")
        print(i)
    else:
        print("outer complete")

def empty_iterable() -> None:
    """Else runs when loop body never executes."""
    items: list[int32] = []
    for x in items:
        break
    else:
        print("empty else")

def var_decl_in_else() -> None:
    """Variable declaration in else block (goto must not cross init)."""
    items: list[int32] = [int32(1), int32(2), int32(3)]
    for item in items:
        if item == int32(99):
            break
    else:
        msg: str = "all checked"
        print(msg)

def main() -> None:
    nums: list[int32] = [int32(1), int32(2), int32(3)]
    search_break(nums, int32(2))
    search_break(nums, int32(99))
    no_break()
    with_continue()
    nested_inner_else()
    nested_outer_else()
    nested_both_else()
    empty_iterable()
    var_decl_in_else()

main()
