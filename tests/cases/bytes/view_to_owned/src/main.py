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

def literal_view() -> None:
    b = b"hello"     # literal -> view (static storage)
    print(b)

def main() -> None:
    process(b"hello")
    augassign(b"world")
    literal_view()

main()
