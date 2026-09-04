# An f-string used directly as a method receiver -- the std::format rvalue
# feeds the native str method's receiver slot, at every value sink.


class Page:
    body: bytes

    def __init__(self) -> None:
        self.body = b""

    def size(self) -> int:
        return len(self.body)


def take(b: bytes) -> int:
    return len(b)


def render(n: int) -> bytes:
    # Return sink for the f-string-receiver method result.
    return f"<p>{n}</p>".encode()  # tpyc: ok


def main() -> None:
    n = 42
    # Local sink: the owned-bytes result of a method on an f-string receiver.
    body = f"<p>epoch: {n}</p>".encode()  # tpyc: ok
    print(len(body))
    # str-returning methods over the same receiver shape.
    print(f"a-{n}-b".upper())  # tpyc: ok
    print(len(f"a-{n}-b".split("-")))  # tpyc: ok
    print(f"key={n}".startswith("key"))  # tpyc: ok
    # Return sink feeding a free-function arg sink.
    print(take(render(n)))  # tpyc: ok
    # Field sink.
    p = Page()
    p.body = f"<h1>{n}</h1>".encode()  # tpyc: ok
    print(p.size())
    # Container-element sink.
    parts = [f"<i>{n}</i>".encode()]  # tpyc: ok
    parts.append(f"<b>{n}</b>".encode())
    print(len(parts), len(parts[0]))


main()
