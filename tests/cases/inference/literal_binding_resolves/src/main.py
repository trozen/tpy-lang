# IntLiteralType is committed to default_int_type at binding sites that feed
# expression analysis: str() picks int32_t (not int8_t), and tuple-unpack
# locals get a concrete C++ type (not the literal value as a "type").

a, b = 1, 2  # tpyc: type(int32)

def main() -> None:
    nums = [10, 20, 30]  # tpyc: type(Array[int32, 3])
    for n in nums:  # tpyc: type(int32)
        print(str(n))

    parts = list(str(x) for x in nums)  # tpyc: type(list[str])
    print(parts)

    # asymmetric widths (200 fits int16 but not int8) -- both resolve to int32.
    p, q = 4, 5  # tpyc: type(int32)
    p2, q2 = 1, 200  # tpyc: type(int32)
    print(str(p), str(q), str(p2), str(q2))

    t = (6, 7)  # tpyc: type(tuple[int32, int32])
    r, s = t  # tpyc: type(int32)
    print(str(r), str(s))

    pairs = [(1, 2), (3, 4)]  # tpyc: type(Array[tuple[int32, int32], 2])
    for u, v in pairs:  # tpyc: type(int32)
        print(str(u), str(v))

    print(str(a), str(b))

main()
