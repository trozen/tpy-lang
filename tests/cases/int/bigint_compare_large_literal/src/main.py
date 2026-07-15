# A BigInt compared against a >int32 literal must render the literal via the
# ambiguity-safe BigInt(static_cast<int64_t>(...)) ctor, not a bare C++ `long`
# (which converts to BigInt ambiguously on macOS, where int64_t is long long).
from tpy import Int64


def main() -> None:
    n: int = 90000000000  # BigInt, > int32
    print(n >= 86400000000)
    print(n <= 86400000000)
    print(n == 86400000000)
    print(n != 86400000000)
    print(n < 86400000000)
    print(n > 86400000000)
    print(-86400000000 <= n)
    print(-86400000000 <= n <= 100000000000)

    # Inverse: fixed-width Int64 vs a large literal stays a plain integer
    # comparison (no BigInt wrap), which the fix must not disturb.
    m: Int64 = 90000000000
    print(m >= 86400000000)


main()
