# Container printing: bools as True/False, floats with .0, strings with quotes
from tpy import Array

def test_bool_list() -> None:
    bools: list[bool] = [True, False, True]
    print(bools)

def test_float_list() -> None:
    floats: list[float] = [1.0, 2.5, 0.0, -3.0]
    print(floats)

def test_str_list() -> None:
    strs: list[str] = ["hello", "world"]
    print(strs)

def test_bool_array() -> None:
    arr: Array[bool, 3] = [True, False, True]
    print(arr)

def test_nested_bool() -> None:
    nested: list[list[bool]] = [[True, False], [False, True]]
    print(nested)

def main() -> None:
    test_bool_list()
    test_float_list()
    test_str_list()
    test_bool_array()
    test_nested_bool()

main()
