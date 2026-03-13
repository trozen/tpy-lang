# str->StrView promoted local must not become string_view in a tuple's str slot
def pick(flag: bool) -> tuple[int, str]:
    label: str = "no"
    if flag:
        label = "yes"
    return 0, label

print(pick(True))
print(pick(False))
