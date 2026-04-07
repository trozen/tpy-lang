# Bytes methods: find, hex, startswith, etc.
def main() -> None:
    data = b"hello world"

    print(data.hex())
    print(data.find(b"world"))
    print(data.find(b"xyz"))
    print(data.rfind(b"l"))
    print(data.count(b"l"))
    print(data.startswith(b"hello"))
    print(data.endswith(b"world"))
    print(data.startswith(b"world"))

    replaced = data.replace(b"world", b"bytes")
    print(replaced)

    parts = b"a,b,c".split(b",")
    for p in parts:
        print(p)

    joined = b", ".join(parts)
    print(joined)

    # BytesView (from slice) -- methods work on views
    v = data[0:11]
    print(v.find(b"world"))
    print(v.replace(b"world", b"there"))
    vparts = data[0:11].split(b" ")
    for vp in vparts:
        print(vp)

main()
