# StrView-promoted local used as dict key or value must become owned str
from tpy import Own

def in_dict_value() -> Own[dict[str, str]]:
    label: str = "no"
    return {"key": label}

def in_dict_key() -> Own[dict[str, int]]:
    k: str = "mykey"
    return {k: 42}

def in_dict_var() -> Own[dict[str, str]]:
    label: str = "no"
    d = {"key": label}
    return d

def in_dict_literal() -> Own[dict[str, str]]:
    label: str = "no"
    return {label: label}

def in_dict_comp() -> Own[dict[str, str]]:
    label: str = "no"
    return {label: label for _ in range(3)}

print(in_dict_value())
print(in_dict_key())
print(in_dict_var())
print(in_dict_literal())
print(in_dict_comp())
