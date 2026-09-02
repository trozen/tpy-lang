# probe-args: --default-int BigInt
def f(s: str) -> str:
    return s[1:3]
def main() -> None:
    print(f('abcd'))
main()
