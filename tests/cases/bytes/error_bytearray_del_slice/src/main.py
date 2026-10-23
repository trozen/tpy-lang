# A slice delete on bytearray, whose type declares no __delitem__
# (BUGS.md#bytearray-del-item-define-delitem), is refused as any delete on
# it is; the slice-delete gap itself is list/error_list_del_slice.
def main() -> None:
    buf = bytearray(b"abcdef")
    i = 2
    del buf[:i]  # tpyc: error(/Type 'bytearray' does not support item deletion/)
    print(buf)


main()
