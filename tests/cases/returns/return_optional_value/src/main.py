from tpy import Int32

def maybe_int(flag: bool) -> Int32 | None:
    if flag:
        return 42
    return None

print(maybe_int(True))
print(maybe_int(False))

result: Int32 | None = maybe_int(True)
if result is not None:
    print(result)

def pass_through(val: Int32 | None) -> Int32 | None:
    return val

print(pass_through(99))
print(pass_through(None))
