# Test that reassigning parameters works correctly for types
# passed as const reference (int/BigInt, str).

def gcd(a: int, b: int) -> int:
    while b != 0:
        t: int = b
        b = a % b
        a = t
    return a

def repeat_str(s: str, n: int) -> str:
    result: str = ""
    i: int = 0
    while i < n:
        result = result + s
        i = i + 1
    return result

def main() -> None:
    print(gcd(48, 18))
    print(gcd(100, 75))
    print(repeat_str("ab", 3))

main()
