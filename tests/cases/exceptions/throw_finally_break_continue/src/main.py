# break and continue inside try-with-finally
from tpy import int32

def test_break() -> None:
    """Break in try body -- finally runs before exiting loop."""
    for i in range(5):
        try:
            if i == 2:
                break
            print(i)
        finally:
            print("cleanup", i)

def test_continue() -> None:
    """Continue in try body -- finally runs before next iteration."""
    for i in range(5):
        try:
            if i == 2:
                continue
            print(i)
        finally:
            print("cleanup", i)

def test_break_for_else() -> None:
    """Break in try body with for-else -- else should be skipped."""
    for i in range(5):
        try:
            if i == 2:
                break
            print(i)
        finally:
            print("cleanup", i)
    else:
        print("else ran")
    print("after loop")

def main() -> None:
    test_break()
    print("---")
    test_continue()
    print("---")
    test_break_for_else()

main()
