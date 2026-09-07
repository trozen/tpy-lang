# An explicit `str.__iter__()` at a for-head and at a print argument: the
# widened view-iterator row is storage-sink only, so neither position has a
# render for it. TPy rejects this for-loop today.
def main() -> None:
    chars: str = "hi"
    for c in chars.__iter__():  # tpyc: error(/expr\.method_call:method\.view\.ret_type/)
        print(c)


main()
