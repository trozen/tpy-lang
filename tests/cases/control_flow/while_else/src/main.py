# while/else: else block runs when condition becomes false (no break)
from tpy import Int32

def no_break() -> None:
    i: Int32 = Int32(0)
    while i < Int32(3):
        print(i)
        i += Int32(1)
    else:
        print("done")

def with_break() -> None:
    i: Int32 = Int32(0)
    while i < Int32(10):
        if i == Int32(5):
            print("broke")
            break
        i += Int32(1)
    else:
        print("completed")

def nested_inner_else() -> None:
    i: Int32 = Int32(0)
    while i < Int32(3):
        j: Int32 = Int32(0)
        while j < Int32(3):
            if j == Int32(1):
                break
            j += Int32(1)
        else:
            print("inner done")
        i += Int32(1)

def nested_outer_else() -> None:
    """Inner break must not affect outer else."""
    i: Int32 = Int32(0)
    while i < Int32(3):
        j: Int32 = Int32(0)
        while j < Int32(3):
            if j == Int32(1):
                break
            j += Int32(1)
        i += Int32(1)
    else:
        print("outer done")

def false_condition() -> None:
    """Else runs when condition is initially false."""
    while False:
        break
    else:
        print("false else")

def var_decl_in_else() -> None:
    """Variable declaration in else block (goto must not cross init)."""
    i: Int32 = Int32(0)
    while i < Int32(10):
        if i == Int32(5):
            break
        i += Int32(1)
    else:
        msg: str = "completed"
        print(msg)

def main() -> None:
    no_break()
    with_break()
    nested_inner_else()
    nested_outer_else()
    false_condition()
    var_decl_in_else()

main()
