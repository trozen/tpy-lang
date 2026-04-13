# Mutual recursion with mix of primitives and records
from tplib import Box

type Value = int | str | Neg

class Neg:
    inner: Box[Value]

def show(v: Value) -> str:
    if isinstance(v, Neg):
        return "-" + show(v.inner.get())
    elif isinstance(v, int):
        return str(v)
    else:
        return v

def main() -> None:
    print(show(42))
    print(show("hello"))

main()
