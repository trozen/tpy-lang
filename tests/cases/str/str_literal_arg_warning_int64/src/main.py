# Under --default-int=int64 a protocol argument warns only past int64's range.
def main() -> None:
    print("in", str(5000000000), str(-2**63))  # tpyc: ok
    print("past", str(2**63))  # tpyc: warning(/literal 9223372036854775808 is outside default int64 range; the argument is passed as int \(BigInt\)\. Write int\(9223372036854775808\)/)


main()
