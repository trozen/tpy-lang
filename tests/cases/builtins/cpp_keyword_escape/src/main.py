# Test that Python identifiers matching C++ reserved keywords are
# escaped correctly in generated code (params, locals, for-loop vars).

from tpy import int32

def get_or_default(x: int32, default: int32) -> int32:
    if x > 0:
        return x
    return default

def test_local_keywords() -> None:
    delete: int32 = 10
    new: int32 = 20
    result: int32 = delete + new
    print(result)

def test_for_loop_keyword() -> None:
    total: int32 = 0
    items: list[int32] = [1, 2, 3]
    for operator in items:
        total = total + operator
    print(total)

def delete(x: int32) -> int32:
    return x * 2

def main() -> None:
    print(get_or_default(5, 42))
    print(get_or_default(-1, 42))
    test_local_keywords()
    test_for_loop_keyword()
    print(delete(7))

main()
