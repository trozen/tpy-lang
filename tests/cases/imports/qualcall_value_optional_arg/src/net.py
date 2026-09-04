def request(url: str, auth: tuple[str, str] | None = None,
            timeout: float | None = None) -> int:
    n = len(url)
    if auth is not None:
        n += len(auth[0])
    if timeout is not None:
        n += int(timeout)
    return n
