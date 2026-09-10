# A `bytearray` at an OWNING `bytes` sink is a located error asking for the
# explicit copy. Neither buffer type converts to the other, so TPy would have to
# copy, while CPython does not enforce the annotation: it binds the bytearray
# OBJECT, so every later read there sees mutations of the source. One position
# only -- compilation stops at the first error; the other sinks and what the
# spelled `bytes(ba)` does are in bytes/bytearray_copy_into_bytes_sink.
def main() -> None:
    ba = bytearray(b"ab")
    b: bytes = ba  # tpyc: error(/write 'bytes\(\.\.\.\)' around it/)
    print(len(b))


main()
