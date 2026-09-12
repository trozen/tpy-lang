# list += [literal] -- BigInt element type requires explicit vector type for C++ deduction
from tpy import int32


def main() -> None:
    # int (BigInt) -- template deduction needs std::vector<BigInt>{...}, not bare {...}
    a: list[int] = [1, 2, 3]
    a += [4, 5]
    print(a)

    # int32 -- works with or without prefix, include for completeness
    b: list[int32] = [10, 20]
    b += [30, 40]
    print(b)

    # str -- const char* doesn't deduce std::string in template deduction context
    c: list[str] = ["a", "b"]
    c += ["c", "d"]
    print(c)


main()
