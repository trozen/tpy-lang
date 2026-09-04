# A whole value-repr Optional local passed to a MODULE-QUALIFIED call: the
# binding already is the `std::optional<T>` the slot takes, so it binds bare.
import net


def split_user(raw: str) -> tuple[str, str]:
    idx = raw.find(":")
    if idx < 0:
        return (raw, "")
    return (raw[:idx], raw[idx + 1:])


def call(user: str, secs: float) -> int:
    auth: tuple[str, str] | None = None
    timeout: float | None = None
    if user != "":
        auth = split_user(user)
    if secs > 0.0:
        timeout = secs
    # Un-narrowed on both paths: the value-TUPLE and the SCALAR inner both
    # pass the whole optional bare into the qualified callee's slot.
    return net.request("http://x", auth, timeout)  # tpyc: ok


def main():
    print(call("user:pw", 2.5), call("", 0.0))


main()
