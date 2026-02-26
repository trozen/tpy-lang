# Test that Python identifiers matching C++ reserved keywords are
# escaped correctly in generated code (params, locals, for-loop vars).

from tpy import Int32

def get_or_default(x: Int32, default: Int32) -> Int32:
    if x > 0:
        return x
    return default

def test_local_keywords() -> None:
    delete: Int32 = 10
    new: Int32 = 20
    result: Int32 = delete + new
    print(result)

def test_for_loop_keyword() -> None:
    total: Int32 = 0
    items: list[Int32] = [1, 2, 3]
    for operator in items:
        total = total + operator
    print(total)

def main() -> None:
    print(get_or_default(5, 42))
    print(get_or_default(-1, 42))
    test_local_keywords()
    test_for_loop_keyword()

main()
