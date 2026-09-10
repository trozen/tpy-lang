# The WRAPPED face of the owning-bytes-sink rule: the buffer type under an
# Optional. Asking the rule once against the annotation misses this, so it is
# asked at the leaf the unwrap recursion reaches -- and the message is the same
# one the bare slot gives. One position only, since compilation stops at the
# first error; the spelled `bytes(ba)` at this and every other sink is in
# bytes/bytearray_copy_into_bytes_sink.
def main() -> None:
    ba = bytearray(b"ab")
    ob: bytes | None = ba  # tpyc: error(/write 'bytes\(\.\.\.\)' around it/)
    print(len(ob) if ob is not None else 0)


main()
