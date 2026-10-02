/**
 * Iterator step-result self-check.
 *
 * `__next__` returns `std::expected<T, StopIteration>`, and the range-for
 * adapter (`NextIterator`) holds that step result directly. Four guarantees
 * are invisible from generated code -- a wrong one is a slowdown, a corrupted
 * source container or a double destroy, never a diagnostic:
 *
 *   1. `StopIteration` is an empty value outside the exception hierarchy, so a
 *      scalar step result is passed in registers.
 *   2. Stepping destroys and re-constructs the held result, never assigns: an
 *      element carrying a reference must not be written THROUGH.
 *   3. A `__next__` that throws mid-iteration leaves the adapter holding
 *      exactly one live result, whether or not the result type can throw on
 *      move.
 *   4. The base of a user return exception is empty, so a class that declares
 *      nothing is as cheap as StopIteration, and its `str()` is empty (a class
 *      declaring `message` gets a generated accessor that hides this one).
 *   5. `next_or` (the two-argument `next`) hands the element on in the form
 *      the iterator steps it: a value by value, a reference payload as a
 *      reference to the ELEMENT or to the DEFAULT itself, never a copy.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdint>
#include <cstdio>
#include <exception>
#include <expected>
#include <stdexcept>
#include <tuple>
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

using scalar_step = std::expected<int64_t, tpy::StopIteration>;
static_assert(std::is_empty_v<tpy::StopIteration>);
static_assert(!std::is_base_of_v<std::exception, tpy::StopIteration>);
// What the C++ ABI needs to pass a class in registers: trivial copy/move
// construction and destruction (std::expected's assignment is never trivial).
static_assert(std::is_trivially_copy_constructible_v<scalar_step>);
static_assert(std::is_trivially_move_constructible_v<scalar_step>);
static_assert(std::is_trivially_destructible_v<scalar_step>);
static_assert(sizeof(scalar_step) <= 2 * sizeof(int64_t));

struct Bare : tpy::ReturnException {};
using bare_step = std::expected<int64_t, Bare>;
static_assert(std::is_empty_v<Bare>);
static_assert(std::is_trivially_copy_constructible_v<bare_step>);
static_assert(std::is_trivially_destructible_v<bare_step>);
static_assert(sizeof(bare_step) <= 2 * sizeof(int64_t));

void bare_return_exception_has_empty_str() {
    check(Bare{}.__str__().empty(), "str() of a class declaring nothing");
}

// Yields (index, reference into `src`): an assigning adapter would write the
// second element through the first element's reference.
struct RefPairs : tpy::next_iter_mixin<RefPairs, std::tuple<int, int&>> {
    std::vector<int>* src;
    size_t i = 0;
    explicit RefPairs(std::vector<int>* s) : src(s) {}
    std::expected<std::tuple<int, int&>, tpy::StopIteration> __next__() {
        if (i >= src->size()) return tpy::make_unexpected(tpy::StopIteration{});
        size_t at = i++;
        return std::tuple<int, int&>(static_cast<int>(at), (*src)[at]);
    }
};

void references_are_not_written_through() {
    std::vector<int> v{10, 20, 30};
    RefPairs it(&v);
    int sum = 0;
    for (auto&& e : it) sum += std::get<1>(e);
    check(sum == 60, "reference elements sum");
    check(v == std::vector<int>({10, 20, 30}), "source untouched by stepping");
}

int live = 0;

template<bool NothrowMove>
struct Counted {
    int v;
    explicit Counted(int x) : v(x) { ++live; }
    Counted(const Counted& o) : v(o.v) { ++live; }
    Counted(Counted&& o) noexcept(NothrowMove) : v(o.v) { ++live; }
    ~Counted() { --live; }
};

template<bool NothrowMove>
struct ThrowsAtThird
    : tpy::next_iter_mixin<ThrowsAtThird<NothrowMove>, Counted<NothrowMove>> {
    int n = 0;
    std::expected<Counted<NothrowMove>, tpy::StopIteration> __next__() {
        if (++n == 3) throw std::runtime_error("third");
        return Counted<NothrowMove>(n);
    }
};

template<bool NothrowMove>
void throwing_step_keeps_one_live_result(const char* what) {
    static_assert(std::is_nothrow_move_constructible_v<
                      std::expected<Counted<NothrowMove>, tpy::StopIteration>>
                  == NothrowMove);
    live = 0;
    int seen = 0;
    bool caught = false;
    try {
        ThrowsAtThird<NothrowMove> it;
        for (auto&& e : it) seen += e.v;
    } catch (const std::runtime_error&) {
        caught = true;
    }
    check(caught && seen == 3, what);
    check(live == 0, "every constructed element destroyed exactly once");
}

// --- 5. next_or keeps the step's form -----------------------------------------

struct Node {
    int v;
    Node(const Node&) = delete;
    explicit Node(int v_) : v(v_) {}
    Node(Node&&) = default;
};

void next_or_keeps_the_step_form() {
    std::vector<int> ints{7};
    auto vi = tpy::__iter__(ints);
    static_assert(std::is_same_v<decltype(tpy::next_or(vi, 0)), int>,
                  "a value payload comes back by value");
    check(tpy::next_or(vi, -1) == 7, "value element");
    check(tpy::next_or(vi, -1) == -1, "value default once exhausted");

    std::vector<Node> nodes;
    nodes.emplace_back(1);
    Node fallback(0);
    auto ni = tpy::__iter__(nodes);
    static_assert(std::is_same_v<decltype(tpy::next_or(ni, fallback)), Node&>,
                  "a reference payload comes back as a reference");
    check(&tpy::next_or(ni, fallback) == &nodes[0], "the element itself");
    check(&tpy::next_or(ni, fallback) == &fallback, "the default itself once exhausted");

    const std::vector<Node>& cnodes = nodes;
    auto ci = tpy::__iter__(cnodes);
    static_assert(std::is_same_v<decltype(tpy::next_or(ci, fallback)), const Node&>,
                  "a const source lends a const element");
    check(&tpy::next_or(ci, fallback) == &nodes[0], "the const element itself");
}

}  // namespace

int main() {
    bare_return_exception_has_empty_str();
    next_or_keeps_the_step_form();
    references_are_not_written_through();
    throwing_step_keeps_one_live_result<true>("throwing step, nothrow-move result");
    throwing_step_keeps_one_live_result<false>("throwing step, throwing-move result");
    if (failures == 0) std::printf("ok\n");
    return failures == 0 ? 0 : 1;
}
