# An unannotated str/bytes local whose storage the deduction DEMOTES to owned
# is dead-on-return storage, rejected exactly as a spelled `bytes` local is.
# Here mutating the sliced source (`ba.append`) demotes `v` to an owned copy,
# so the `-> BytesView` signature would hand back a view of a local that dies
# at the return. The check waits for the storage verdict rather than reading
# the local's still-undecided binding type, which would have called it a view.
from tpy import BytesView


def demoted_by_source_mutation(ba: bytearray) -> BytesView:
    v = ba[1:]
    ba.append(5)
    return v  # tpyc: error(/BytesView referencing a local or temporary/)


def main() -> None:
    print(bytes(demoted_by_source_mutation(
        bytearray(b"0123456789abcdefghijklmnop"))))


main()
