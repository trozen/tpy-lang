# StrView-promoted local used in list[str], list[tuple[int, str]], and list[tuple[str, tuple[str, int]]] must become owned str
from tpy import Own

def in_list() -> Own[list[str]]:
    label: str = "no"
    return [label]

def in_tuple_list() -> Own[list[tuple[int, str]]]:
    label: str = "no"
    return [(0, label)]

def in_list_var() -> Own[list[str]]:
    label: str = "no"
    xs = [label]
    return xs

def in_tuple_list_var() -> Own[list[tuple[int, str]]]:
    label: str = "no"
    xs = [(0, label)]
    return xs

def in_list_repeat() -> Own[list[str]]:
    label: str = "no"
    return [label] * 3

def in_list_comp() -> Own[list[str]]:
    label: str = "no"
    return [label for _ in range(3)]

def in_nested_tuple_list() -> Own[list[tuple[str, tuple[str, int]]]]:
    label: str = "x"
    return [(label, (label, 42))]

print(in_list())
print(in_tuple_list())
print(in_list_var())
print(in_tuple_list_var())
print(in_list_repeat())
print(in_list_comp())
print(in_nested_tuple_list())
