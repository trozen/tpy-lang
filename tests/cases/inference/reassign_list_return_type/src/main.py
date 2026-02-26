# Return-type-driven list deduction
from tpy import Own

def get_items() -> Own[list[int]]:
    xs = [1, 2, 3]  # tpyc: type(/list/)
    return xs

items = get_items()
print(len(items))
