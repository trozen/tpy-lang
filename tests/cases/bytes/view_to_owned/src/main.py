# Bytes view optimization: param assignment stays as span,
# concat/augassign promotes to owned vector
def process(data: bytes) -> None:
    b = data         # should be span (view)
    print(b)
    c = data + b"!"  # concat -> owned
    print(c)

def augassign(data: bytes) -> None:
    b = data         # starts as view
    b += b"!"        # augassign promotes to owned
    print(b)

def literal_copy() -> None:
    b = b"hello"     # literal -> owned (not view-safe)
    print(b)

def main() -> None:
    process(b"hello")
    augassign(b"world")
    literal_copy()

main()
