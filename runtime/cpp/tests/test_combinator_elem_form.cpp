/**
 * Combinator element-form self-check.
 *
 * `enumerate` / `zip` / `map` / `filter` / `reversed` hand an element on in
 * the form their SOURCE steps it, never in a form the compiler names:
 *
 *   1. A begin()/end() container lends its element with its own const: a
 *      mutable container yields `Rec&`, a const one `const Rec&`, a `Span`
 *      of either -- in the lvalue, owning and mixed flavors alike.
 *   2. An iterator source is passed through: a lent step (`val_or_ref<X>`,
 *      const included) stays lent, a fresh value moves on as a value, and a
 *      nested combinator lends what its own source lent.
 *   3. `reversed` lends through `__getitem__` (const included) and builds a
 *      `char` off a `str`.
 *   4. `map` hands on what its callable hands back in the step form the
 *      compiler declares for that return: a reference is lent, a value owned.
 *   5. A value type is carried by value everywhere, a `vector<bool>` proxy
 *      as `bool`.
 *   6. A mutation through the element reaches the source container, in
 *      every lending flavor (the runtime half of CPython's aliasing).
 *   7. A collect into storage (`list(enumerate(xs))`) COPIES a lent element
 *      and never moves it out from under the source; a nested combinator
 *      steps its inner tuple as a member.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <array>
#include <cstdint>
#include <map>
#include <cstdio>
#include <expected>
#include <span>
#include <string>
#include <tuple>
#include <type_traits>
#include <utility>
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

// A reference type (not a value type): what a compiled record is.
struct Rec {
    int32_t v;
};

using Recs = std::vector<Rec>;
using RecArr = std::array<Rec, 2>;
using Ints = std::vector<int32_t>;

// A self-iterator counting 1..n by value.
struct Counter : tpy::next_iter_mixin<Counter, int32_t> {
    int32_t i = 0;
    int32_t n;
    explicit Counter(int32_t n) : n(n) {}
    std::expected<int32_t, tpy::StopIteration> __next__() {
        if (i >= n) return tpy::make_unexpected(tpy::StopIteration{});
        return ++i;
    }
    Counter& __iter__() { return *this; }
};

// A self-iterator lending elements of a container it points at, mutable or
// const: the shape of a compiled record's iterator over a member list.
template<typename C>
struct Lender : tpy::next_iter_mixin<Lender<C>, tpy::val_or_ref<std::remove_reference_t<decltype(*std::declval<C&>().begin())>>> {
    using elem_t = std::remove_reference_t<decltype(*std::declval<C&>().begin())>;
    C* c;
    std::size_t i = 0;
    explicit Lender(C& c) : c(&c) {}
    std::expected<tpy::val_or_ref<elem_t>, tpy::StopIteration> __next__() {
        if (i >= c->size()) return tpy::make_unexpected(tpy::StopIteration{});
        return tpy::val_or_ref<elem_t>((*c)[i++]);
    }
    Lender& __iter__() { return *this; }
};

// A self-iterator building a fresh Rec each step.
struct Maker : tpy::next_iter_mixin<Maker, Rec> {
    int32_t i = 0;
    std::expected<Rec, tpy::StopIteration> __next__() {
        if (i >= 2) return tpy::make_unexpected(tpy::StopIteration{});
        return Rec{++i};
    }
    Maker& __iter__() { return *this; }
};

bool big(const Rec& r) { return r.v > 1; }
bool odd(int32_t v) { return v % 2 != 0; }
Rec& same(Rec& r) { return r; }
const Rec& csame(const Rec& r) { return r; }
Rec fresh(const Rec& r) { return Rec{r.v * 10}; }
int32_t val(const Rec& r) { return r.v; }
int32_t add(int32_t a, int32_t b) { return a + b; }

template<typename It>
using step_t = typename decltype(std::declval<It&>().__next__())::value_type;

template<typename... Args>
using enumerate_step_t = step_t<decltype(tpy::builtin_enumerate(std::declval<Args>()...))>;
template<typename... Args>
using zip_step_t = step_t<decltype(tpy::builtin_zip(std::declval<Args>()...))>;
template<typename Src>
using reversed_step_t = step_t<decltype(tpy::builtin_reversed(std::declval<Src>()))>;
template<typename Fn, typename Src>
using filter_step_t = step_t<decltype(tpy::builtin_filter(std::declval<Fn>(), std::declval<Src>()))>;
template<typename U, typename Fn, typename Src>
using map_step_t = step_t<decltype(tpy::builtin_map<U>(std::declval<Fn>(), std::declval<Src>()))>;

// 1. Containers lend with their own const, in every flavor.
static_assert(std::is_same_v<enumerate_step_t<Recs&>, std::tuple<int32_t, Rec&>>);
static_assert(std::is_same_v<enumerate_step_t<const Recs&>, std::tuple<int32_t, const Rec&>>);
// An owning flavor lends its member as OWNED (an rvalue reference): a `for`
// binds it as an lvalue, a collect moves it out of the dying container.
static_assert(std::is_same_v<enumerate_step_t<Recs>, std::tuple<int32_t, Rec&&>>);
static_assert(std::is_same_v<enumerate_step_t<RecArr&>, std::tuple<int32_t, Rec&>>);
static_assert(std::is_same_v<enumerate_step_t<const RecArr&>, std::tuple<int32_t, const Rec&>>);
static_assert(std::is_same_v<enumerate_step_t<std::span<Rec>&>, std::tuple<int32_t, Rec&>>);
static_assert(std::is_same_v<enumerate_step_t<std::span<const Rec>&>, std::tuple<int32_t, const Rec&>>);
static_assert(std::is_same_v<zip_step_t<Recs&, const Recs&>, std::tuple<Rec&, const Rec&>>);
// Mixed flavor: the owned temporary lends through its own iterator, the
// lvalue beside it lends with its const.
static_assert(std::is_same_v<zip_step_t<const Recs&, Recs>, std::tuple<const Rec&, Rec&&>>);
static_assert(std::is_same_v<filter_step_t<decltype(&big), Recs&>, tpy::val_or_ref<Rec>>);
static_assert(std::is_same_v<filter_step_t<decltype(&big), const Recs&>, tpy::val_or_ref<const Rec>>);
static_assert(std::is_same_v<filter_step_t<decltype(&big), Recs>, tpy::val_or_ref<Rec>>);

// 2. Iterator sources pass their step through; nested combinators lend on.
static_assert(std::is_same_v<enumerate_step_t<Counter&>, std::tuple<int32_t, int32_t>>);
static_assert(std::is_same_v<enumerate_step_t<Lender<Recs>&>, std::tuple<int32_t, Rec&>>);
static_assert(std::is_same_v<enumerate_step_t<Lender<const Recs>&>, std::tuple<int32_t, const Rec&>>);
static_assert(std::is_same_v<enumerate_step_t<Maker&>, std::tuple<int32_t, Rec>>);
static_assert(std::is_same_v<enumerate_step_t<Maker>, std::tuple<int32_t, Rec>>);
static_assert(std::is_same_v<zip_step_t<Lender<Recs>, Maker>, std::tuple<Rec&, Rec>>);
// An owned self-iterator that LENDS from elsewhere is not owned storage: the
// lent step stays a plain reference. So is a borrowed range held by value (a
// span, a dict view): its elements are the caller's.
static_assert(std::is_same_v<enumerate_step_t<Lender<Recs>>, std::tuple<int32_t, Rec&>>);
static_assert(std::is_same_v<enumerate_step_t<std::span<Rec>>, std::tuple<int32_t, Rec&>>);
static_assert(std::is_same_v<zip_step_t<std::span<Rec>, Ints&>, std::tuple<Rec&, int32_t>>);
static_assert(std::is_same_v<
    enumerate_step_t<decltype(tpy::dict_values(std::declval<tpy::ordered_map<int32_t, Rec>&>()))>,
    std::tuple<int32_t, Rec&>>);
// A nested OWNING step is handed on in its out-form: the inner owned member
// becomes a value the outer tuple holds, so nothing points at the inner step.
static_assert(std::is_same_v<enumerate_step_t<decltype(tpy::builtin_enumerate(std::declval<Recs>()))>,
                             std::tuple<int32_t, std::tuple<int32_t, Rec>>>);
static_assert(std::is_same_v<
    zip_step_t<decltype(tpy::builtin_enumerate(std::declval<Recs>())), Ints&>,
    std::tuple<std::tuple<int32_t, Rec>, int32_t>>);
static_assert(std::is_same_v<
    enumerate_step_t<decltype(tpy::builtin_filter(&big, std::declval<Recs&>()))>,
    std::tuple<int32_t, Rec&>>);
static_assert(std::is_same_v<
    zip_step_t<decltype(tpy::builtin_filter(&big, std::declval<const Recs&>())), Ints&>,
    std::tuple<const Rec&, int32_t>>);
static_assert(std::is_same_v<filter_step_t<decltype(&big), Lender<Recs>&>, tpy::val_or_ref<Rec>>);
static_assert(std::is_same_v<filter_step_t<decltype(&big), Maker>, Rec>);

// 3. reversed lends through __getitem__.
static_assert(std::is_same_v<reversed_step_t<Recs&>, tpy::val_or_ref<Rec>>);
static_assert(std::is_same_v<reversed_step_t<const Recs&>, tpy::val_or_ref<const Rec>>);
static_assert(std::is_same_v<reversed_step_t<Recs>, tpy::val_or_ref<Rec>>);
static_assert(std::is_same_v<reversed_step_t<Ints&>, int32_t>);
static_assert(std::is_same_v<reversed_step_t<std::string&>, char>);

// 4. map hands on what the callable hands back, in the step form the compiler
// declares for the callable's return (`U`): a borrow return lends.
static_assert(std::is_same_v<map_step_t<tpy::val_or_ref<Rec>, decltype(&same), Recs&>, tpy::val_or_ref<Rec>>);
static_assert(std::is_same_v<map_step_t<tpy::val_or_ref<const Rec>, decltype(&csame), Recs&>, tpy::val_or_ref<const Rec>>);
static_assert(std::is_same_v<map_step_t<Rec, decltype(&fresh), Recs&>, Rec>);
static_assert(std::is_same_v<map_step_t<int32_t, decltype(&val), const Recs&>, int32_t>);
static_assert(std::is_same_v<
    step_t<decltype(tpy::builtin_map_n<int32_t>(&add, std::declval<Ints&>(), std::declval<Ints&>()))>, int32_t>);

// 5. Value types by value; a vector<bool> proxy as bool. The `str` row pins
// the CURRENT form: an owning copy per step where a lent view would do
// (BUGS.md#combinator-str-element-copied-per-step).
static_assert(std::is_same_v<enumerate_step_t<Ints&>, std::tuple<int32_t, int32_t>>);
static_assert(std::is_same_v<enumerate_step_t<const Ints&>, std::tuple<int32_t, int32_t>>);
static_assert(std::is_same_v<enumerate_step_t<std::vector<bool>&>, std::tuple<int32_t, bool>>);
static_assert(std::is_same_v<enumerate_step_t<std::vector<std::string>&>, std::tuple<int32_t, std::string>>);
static_assert(std::is_same_v<zip_step_t<Ints&, std::vector<bool>&>, std::tuple<int32_t, bool>>);
static_assert(std::is_same_v<filter_step_t<decltype(&odd), Ints&>, int32_t>);

// 6. A mutation through the element reaches the source.
void enumerate_lends() {
    Recs rs{{1}, {2}};
    for (auto&& [i, r] : tpy::builtin_enumerate(rs)) r.v += 10 + i;
    check(rs[0].v == 11 && rs[1].v == 13, "enumerate over an lvalue lends");
    const Recs& crs = rs;
    int32_t sum = 0;
    for (auto&& [i, r] : tpy::builtin_enumerate(crs)) sum += r.v;
    check(sum == 24, "enumerate over a const lvalue reads through const");
}

void zip_lends() {
    Recs rs{{1}, {2}};
    Ints ws{5, 6};
    for (auto&& [r, w] : tpy::builtin_zip(rs, ws)) r.v += w;
    check(rs[0].v == 6 && rs[1].v == 8, "direct zip lends");
    for (auto&& [r, w] : tpy::builtin_zip(rs, Ints{1, 1})) r.v += w;
    check(rs[0].v == 7 && rs[1].v == 9, "mixed zip lends its lvalue");
    Lender<Recs> ld(rs);
    for (auto&& [i, r] : tpy::builtin_enumerate(ld)) r.v += 100;
    check(rs[0].v == 107 && rs[1].v == 109, "an iterator source's lent step is lent on");
}

void filter_and_reversed_lend() {
    Recs rs{{1}, {2}};
    for (auto&& r : tpy::builtin_filter(&big, rs)) r.v += 10;
    check(rs[0].v == 1 && rs[1].v == 12, "direct filter lends");
    for (auto&& r : tpy::builtin_reversed(rs)) r.v += 100;
    check(rs[0].v == 101 && rs[1].v == 112, "reversed lends");
    for (auto&& r : tpy::builtin_map<tpy::val_or_ref<Rec>>(&same, rs)) r.v += 1000;
    check(rs[0].v == 1101 && rs[1].v == 1112, "map lends a returned reference");
    Recs::size_type n = 0;
    for (auto&& [i, r] : tpy::builtin_enumerate(tpy::builtin_filter(&big, rs))) {
        r.v = 0;
        ++n;
    }
    check(n == 2 && rs[0].v == 0 && rs[1].v == 0, "enumerate over filter lends on");
}

void owned_sources_lend_and_values_copy() {
    int32_t sum = 0;
    for (auto&& [i, r] : tpy::builtin_enumerate(Recs{{3}, {4}})) {
        r.v += 1;
        sum += r.v;
    }
    check(sum == 9, "an owning enumerate lends into what it owns");
    std::vector<bool> bs{true, false, true};
    int32_t trues = 0;
    for (auto&& [i, b] : tpy::builtin_enumerate(bs)) trues += b ? 1 : 0;
    check(trues == 2, "a vector<bool> proxy is carried as bool");
    Ints is{1, 2, 3};
    for (auto&& [i, v] : tpy::builtin_enumerate(is)) v += 10;
    check(is[0] == 1, "a value element is a copy");
}

// 7. Collect copies lent members; nested tuple steps.
struct Bag {
    std::vector<int32_t> items;
};

void collect_copies_lent_members() {
    std::vector<Bag> bags{{{1, 2}}, {{3}}};
    auto pairs = tpy::collect<std::vector<std::tuple<int32_t, Bag>>>(tpy::builtin_enumerate(bags));
    check(bags[0].items.size() == 2 && bags[1].items.size() == 1,
          "collect leaves the source's elements intact");
    check(std::get<1>(pairs[0]).items.size() == 2, "collect holds a copy");
    std::get<1>(pairs[0]).items.clear();
    check(bags[0].items.size() == 2, "the collected copy is not the source");
    std::vector<int32_t> ws{7};
    auto zs = tpy::collect<std::vector<std::tuple<Bag, int32_t>>>(tpy::builtin_zip(bags, ws));
    check(bags[0].items.size() == 2 && std::get<0>(zs[0]).items.size() == 2,
          "direct zip collect copies too");
    // An owning combinator's collect MOVES out of the container it owns: a
    // move-only element collects, and a moved-out string is empty.
    struct MoveOnly {
        std::vector<int32_t> items;
        MoveOnly(MoveOnly&&) = default;
        MoveOnly(const MoveOnly&) = delete;
        explicit MoveOnly(int32_t n) : items{n} {}
    };
    std::vector<MoveOnly> mos;
    mos.emplace_back(4);
    auto owned = tpy::collect<std::vector<std::tuple<int32_t, MoveOnly>>>(
        tpy::builtin_enumerate(std::move(mos)));
    check(std::get<1>(owned[0]).items.size() == 1, "an owning enumerate collects a move-only element");
    // A `val_or_ref` member over a VALUE payload is the wrapper's own copy and
    // moves out; a reference payload stays lent.
    static_assert(std::is_same_v<
        decltype(tpy::unwrap_ref_move(std::declval<std::tuple<int32_t, tpy::val_or_ref<std::string>>&>())),
        std::tuple<int32_t, std::string>>);
    static_assert(std::is_same_v<
        decltype(tpy::unwrap_ref_move(std::declval<std::tuple<int32_t, tpy::val_or_ref<Rec>>&>())),
        std::tuple<int32_t, Rec&>>);
    // A borrowed range held by value keeps lending: the collect copies.
    Recs spanned{{5}, {6}};
    auto sp = tpy::collect<std::vector<std::tuple<int32_t, Rec>>>(
        tpy::builtin_enumerate(std::span<Rec>(spanned)));
    check(spanned[0].v == 5 && std::get<1>(sp[1]).v == 6, "an owned span lends the caller's elements");
    tpy::ordered_map<int32_t, Bag> m;
    m[1] = Bag{{9, 9}};
    auto dv = tpy::collect<std::vector<std::tuple<int32_t, Bag>>>(
        tpy::builtin_enumerate(tpy::dict_values(m)));
    check(m[1].items.size() == 2 && std::get<1>(dv[0]).items.size() == 2,
          "an owned dict view lends the map's elements");
    // A nested owning step: the inner element is moved once, into the outer
    // tuple, and read back whole.
    int32_t inner_sum = 0;
    for (auto&& [i, pr] : tpy::builtin_enumerate(tpy::builtin_enumerate(Recs{{7}, {8}})))
        inner_sum += std::get<1>(pr).v + i;
    check(inner_sum == 16, "enumerate over an owning enumerate reads the inner element");
    auto nested = tpy::collect<std::vector<std::tuple<int32_t, std::tuple<int32_t, Rec>>>>(
        tpy::builtin_enumerate(tpy::builtin_enumerate(Recs{{7}, {8}})));
    check(std::get<1>(std::get<1>(nested[1])).v == 8, "a nested owning collect holds the value");
    // A lent member of a nested step stays lent: enumerate over zip.
    static_assert(std::is_same_v<
        enumerate_step_t<decltype(tpy::builtin_zip(std::declval<Recs&>(), std::declval<Ints&>()))>,
        std::tuple<int32_t, std::tuple<Rec&, int32_t>>>);
    Recs rs{{1}, {2}};
    Ints is{10, 20};
    for (auto&& [i, pr] : tpy::builtin_enumerate(tpy::builtin_zip(rs, is))) std::get<0>(pr).v += i + 100;
    check(rs[0].v == 101 && rs[1].v == 103, "enumerate over zip lends the inner member");
}

} // namespace

int main() {
    enumerate_lends();
    zip_lends();
    filter_and_reversed_lend();
    owned_sources_lend_and_values_copy();
    collect_copies_lent_members();
    if (failures) return 1;
    std::printf("ok\n");
    return 0;
}
