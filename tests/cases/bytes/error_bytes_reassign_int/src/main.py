# Bytes parallel of str/error_str_reassign_int: a bytes-view local
# (PendingBytesType, a PendingViewType subclass) reassigned to an int must be
# rejected at sema, not deferred to the C++ compile step.
def main() -> None:
    b = b"hi"
    b = 5  # tpyc: error(/Type mismatch in reassignment to 'b': expected bytes/)
    print(b)

main()
