# StrView-promoted local used in set[str] and set comprehension must become owned str
from tpy import Own

def in_set() -> Own[set[str]]:
    label: str = "x"
    return {label}

def in_set_comp() -> Own[set[str]]:
    label: str = "x"
    return {label for _ in range(3)}

print(in_set())
print(in_set_comp())
