"""Callable-reference bundle pins: the generic fn-ref template-args suffix,
the MIL callable-lambda field row, and the async coroutine-factory wrapper
synthesis (name/return/ctor positions). Corpus witnesses:
calls/func_ref_generic, calls/callable_field_print, and the three
async/async_fn_as_callable_* cases (ratchet-pinned once flipped)."""

from .testutil import _compile, _entry, _lower_ctx, _fn
from ..codegen_cpp import CodeGenOptions

_PRELUDE = "from typing import Callable\nfrom tpy import Int32\n"


def _gen(src: str):
    compiler, modules = _compile(src)
    hpp, cpp = compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False))
    return compiler, hpp + cpp


def _assert_routes_identical(src: str, *names: str) -> str:
    thir = _lower_ctx(src)
    for n in names:
        assert _fn(thir, n) is not None, n
    _, ast_out = _gen(src)
    compiler, thir_out = _gen(src)
    assert ast_out == thir_out
    return thir_out


class TestGenericFuncRefTargs:
    def test_targs_suffix_routes_all_positions(self):
        # decl init, return slot, and call arg all spell `identity<int32_t>`.
        src = _PRELUDE + (
            "def identity[T](x: T) -> T:\n    return x\n"
            "def apply(f: Callable[[Int32], Int32], x: Int32) -> Int32:\n"
            "    return f(x)\n"
            "def get() -> Callable[[Int32], Int32]:\n"
            "    return identity\n"
            "def use() -> None:\n"
            "    g: Callable[[Int32], Int32] = identity\n"
            "    print(g(1))\n"
            "    print(apply(identity, 2))\n"
            "    print(get()(3))\n")
        out = _assert_routes_identical(src, "apply", "get", "use")
        assert out.count("identity<int32_t>") >= 3


class TestMilCallableLambda:
    def test_lambda_field_init_routes(self):
        src = _PRELUDE + (
            "class Handler:\n"
            "    action: Callable[[], None]\n"
            "    def __init__(self) -> None:\n"
            "        self.action = lambda: print(0)\n"
            "def use(h: Handler) -> None:\n"
            "    h.action()\n")
        out = _assert_routes_identical(src, "use")
        assert ": action([]() { std::cout << 0" in out


class TestAsyncFactoryWrap:
    _PRE = (
        "import asyncio\n"
        "from typing import Callable\n"
        "from tpy import Int32, Own\n"
        "from tpy.coro import Cancellable\n"
        "async def triple(n: Int32) -> Int32:\n"
        "    await asyncio.sleep(0.0)\n"
        "    return n + n + n\n")

    def test_return_position_synthesizes_the_wrapper(self):
        src = self._PRE + (
            "def pick() -> Callable[[Int32], Own[Cancellable[Int32]]]:\n"
            "    return triple\n"
            "async def main_coro() -> Int32:\n"
            "    factory = pick()\n"
            "    return await asyncio.create_task(factory(7))\n"
            "def main() -> None:\n"
            "    print(asyncio.run(main_coro()))\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "pick") is not None
        _, ast_out = _gen(src)
        compiler, thir_out = _gen(src)
        assert ast_out == thir_out
        assert ("return [](int32_t __a0) -> "
                "std::unique_ptr<::tpystd::coro::Cancellable<int32_t>> { "
                "return ::tpy::make_adapter<::tpystd::coro::Cancellable"
                "<int32_t>>(triple(__a0)); };") in thir_out
