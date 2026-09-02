from tpy import Int32
def ident[T](x: T) -> T:
    return x
def pick[K, V](k: K, v: V) -> tuple[K, V]:
    return (k, ident(v))
def main() -> None:
    t = pick(1, "a")
    print(t[0])
main()
