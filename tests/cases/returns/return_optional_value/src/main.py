from tpy import int32

def maybe_int(flag: bool) -> int32 | None:
    if flag:
        return 42
    return None

print(maybe_int(True))
print(maybe_int(False))

result: int32 | None = maybe_int(True)
if result is not None:
    print(result)

def pass_through(val: int32 | None) -> int32 | None:
    return val

print(pass_through(99))
print(pass_through(None))
