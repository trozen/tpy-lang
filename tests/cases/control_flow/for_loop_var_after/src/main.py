# Loop variable and body-declared variables visible after the loop
from tpy import Int32


def test_range_var() -> None:
    for i in range(5):
        pass
    print(i)


def test_body_var() -> None:
    for i in range(3):
        x: Int32 = i * 10
    print(x)


def test_list_iteration() -> None:
    items = [10, 20, 30]
    for v in items:
        last = v
    print(last)


def test_break() -> None:
    for i in range(10):
        if i == 5:
            break
    print(i)


def test_nested() -> None:
    for i in range(3):
        for j in range(3):
            pass
    print(i)
    print(j)


def test_record_in_body() -> None:
    items = ["a", "b", "c"]
    for s in items:
        msg = s + "!"
    print(msg)


def test_sequential_same_var() -> None:
    for i in range(3):
        pass
    for i in range(5):
        pass
    print(i)


def test_str_loop_var() -> None:
    items = ["a", "b", "c"]
    for s in items:
        pass
    print(s)


def test_tuple_unpack() -> None:
    items: list[tuple[int, str]] = [(1, "a"), (2, "b"), (3, "c")]
    for i, s in items:
        pass
    print(i)
    print(s)


def test_tuple_unpack_partial() -> None:
    """Only one unpack var used after loop."""
    items: list[tuple[int, str]] = [(1, "a"), (2, "b")]
    for i, s in items:
        pass
    print(i)


def test_tuple_unpack_second_func() -> None:
    """Ensures loop_hoisted_vars is reset between functions."""
    items: list[tuple[int, str]] = [(10, "x"), (20, "y")]
    for i, s in items:
        pass
    print(i)
    print(s)


test_range_var()
test_body_var()
test_list_iteration()
test_break()
test_nested()
test_record_in_body()
test_sequential_same_var()
test_str_loop_var()
test_tuple_unpack()
test_tuple_unpack_partial()
test_tuple_unpack_second_func()
