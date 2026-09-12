from tpy import int32

x: int32 | None = None
print(x is None)
print(x)
x = 42
print(x is None)
print(x)

y: int32 | None = 10
print(y)

b: bool | None = None
print(b)
b = True
print(b)

f: float | None = None
print(f)
f = 3.14
print(f)

# 0 must be distinct from None (std::optional<int>(0) has a value)
z: int32 | None = 0
print(z is None)
print(z)

# Global-to-local: must stay on value path, no pointer-local indirection
def use_global() -> None:
    local: int32 | None = y
    print(local)
    inferred = y
    print(inferred is None)

use_global()
