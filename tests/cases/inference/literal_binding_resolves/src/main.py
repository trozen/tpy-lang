# IntLiteralType is committed to default_int_type at binding sites that feed
# expression analysis: str() picks int32_t (not int8_t), and tuple-unpack
# locals get a concrete C++ type (not the literal value as a "type").

a, b = 1, 2  # tpyc: type(Int32)

def main() -> None:
    nums = [10, 20, 30]  # tpyc: type(Array[Int32, 3])
    for n in nums:  # tpyc: type(Int32)
        print(str(n))

    parts = list(str(x) for x in nums)  # tpyc: type(list[str])
    print(parts)

    # asymmetric widths (200 fits Int16 but not Int8) -- both resolve to Int32.
    p, q = 4, 5  # tpyc: type(Int32)
    p2, q2 = 1, 200  # tpyc: type(Int32)
    print(str(p), str(q), str(p2), str(q2))

    t = (6, 7)  # tpyc: type(tuple[Int32, Int32])
    r, s = t  # tpyc: type(Int32)
    print(str(r), str(s))

    pairs = [(1, 2), (3, 4)]  # tpyc: type(Array[tuple[Int32, Int32], 2])
    for u, v in pairs:  # tpyc: type(Int32)
        print(str(u), str(v))

    print(str(a), str(b))

main()
