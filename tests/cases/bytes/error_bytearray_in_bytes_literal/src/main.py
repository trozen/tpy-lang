# The container-LITERAL element face of the owning-bytes-sink rule. It is its
# own case because compilation stops at the first error and
# bytes/error_bytearray_at_bytes_sink takes the annotated local. The message is
# the list-literal seam's own framing rather than the `bytes(...)` remediation
# the sibling sinks give: that seam rewrites every targeted diagnostic raised
# under it (BUGS.md#literal-elem-seam-swallows-remediation).
def main() -> None:
    ba = bytearray(b"ab")
    xs: list[bytes] = [ba]  # tpyc: error(/incompatible with annotated element type bytes/)
    print(len(xs))


main()
