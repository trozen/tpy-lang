/**
 * as_mut_lvalue self-check.
 *
 * Generated code binds a fresh argument at a parameter its callee writes, or
 * whose address it takes, through `tpy::as_mut_lvalue`: the helper must hand
 * back the temporary itself as a mutable lvalue, so the write lands in it and
 * the temporary is destroyed at the end of the full-expression, never
 * earlier, never later. A mutable lvalue passes through as itself; a const
 * lvalue is a compile error, pinned by
 * `test_as_mut_lvalue_rejects_a_const_lvalue` in tests/test_runtime_cpp.py.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdio>
#include <type_traits>
#include <vector>

#include "tpy/tpy.hpp"

namespace {

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

int destroyed = 0;

struct Tracked {
    int v = 1;
    ~Tracked() { ++destroyed; }
};

struct Base { int v = 1; };
struct Derived : Base { int w = 2; };

int grow(std::vector<int>& xs) {
    xs.push_back(4);
    return static_cast<int>(xs.size());
}

int poke(Tracked* t) {
    t->v = 9;
    // The temporary is still alive while the call runs.
    return destroyed == 0 ? t->v : -1;
}

int base_v(Base& b) { return b.v; }

}  // namespace

int main() {
    static_assert(std::is_same_v<decltype(tpy::as_mut_lvalue(std::vector<int>{})),
                                 std::vector<int>&>);
    check(grow(tpy::as_mut_lvalue(std::vector<int>{1, 2, 3})) == 4,
          "a written parameter writes the temporary itself");

    check(poke(&tpy::as_mut_lvalue(Tracked{})) == 9,
          "an address-taken temporary lives for the call");
    check(destroyed == 1, "the temporary dies at the end of its full-expression");

    check(base_v(tpy::as_mut_lvalue(Derived{})) == 1,
          "a subclass temporary binds a base reference");

    std::vector<int> named{1};
    static_assert(std::is_same_v<decltype(tpy::as_mut_lvalue(named)),
                                 std::vector<int>&>);
    check(&tpy::as_mut_lvalue(named) == &named,
          "a mutable lvalue passes through as itself");

    if (failures == 0) std::printf("ok\n");
    return failures == 0 ? 0 : 1;
}
