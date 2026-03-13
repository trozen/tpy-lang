# StrView-promoted local used in nested tuple's str slot must become owned str
def pick(flag: bool) -> tuple[int, tuple[str, bool]]:
    label: str = "no"
    if flag:
        label = "yes"
    return 0, (label, flag)
print(pick(True))
print(pick(False))
