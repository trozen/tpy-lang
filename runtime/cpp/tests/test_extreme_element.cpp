/**
 * Element-returning min/max self-check.
 *
 * `builtin_{min,max}_elem[_key]` return the winning element ITSELF, as
 * CPython returns the object, so their result is a reference whose form and
 * identity no snapshot can see -- a wrong one is a silent copy, a const hole
 * or a dangling reference. One leg per kind of source tpyc can hand them:
 *
 *   1. Containers: a mutable lvalue lends `T&`, a const one `const T&`; an
 *      inline container (`std::array`); a runtime view (`std::span<T>` and
 *      `std::span<const T>`); a set and a dict's keys lend `const T&`.
 *   2. Iterator sources through `iter_range` -- a self-iterator stepping
 *      reference payloads (mutable and const), a record whose `__iter__`
 *      returns a separate iterator -- get the winner BY VALUE: a step is
 *      valid only until the next one, and a separate iterator dies inside
 *      the helper. A result the compiler binds as a borrow passes through
 *      `assert_lent`, which accepts only a reference: a lending source's
 *      result does, the same element.
 *   3. An rvalue container: a reference into it, copied out within the full
 *      expression.
 *   4. An element that can be neither copied nor moved.
 *   5. Semantics: the first of equal elements wins for min and max, the key
 *      runs once per element in order, any size change of the source -- or
 *      a change of a contiguous source's storage at an equal size (grow past
 *      capacity, shrink back) -- after a key call or a comparison raises
 *      RuntimeError, and an empty source raises ValueError.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <array>
#include <cstdint>
#include <cstdio>
#include <expected>
#include <span>
#include <string>
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

struct Node {
    int32_t v;
    int32_t tag = 0;
    bool operator<(const Node& o) const { return v < o.v; }
};

auto by_v = [](const Node& n) { return n.v; };

// --- 1. containers ------------------------------------------------------------

void containers() {
    std::vector<Node> v{{2}, {5}, {1}, {4}};
    static_assert(std::is_same_v<decltype(tpy::builtin_max_elem<Node>(v)), Node&>);
    static_assert(std::is_same_v<decltype(tpy::builtin_min_elem_key<Node>(v, by_v)), Node&>);
    check(&tpy::builtin_max_elem<Node>(v) == &v[1], "vector: max is the element");
    check(&tpy::builtin_min_elem<Node>(v) == &v[2], "vector: min is the element");
    check(&tpy::builtin_max_elem_key<Node>(v, by_v) == &v[1], "vector: keyed max");
    check(&tpy::builtin_min_elem_key<Node>(v, by_v) == &v[2], "vector: keyed min");
    tpy::builtin_max_elem<Node>(v).tag = 7;
    check(v[1].tag == 7, "vector: a write through the result reaches the source");

    const std::vector<Node>& cv = v;
    static_assert(std::is_same_v<decltype(tpy::builtin_max_elem<Node>(cv)), const Node&>);
    static_assert(std::is_same_v<decltype(tpy::builtin_max_elem_key<Node>(cv, by_v)),
                                 const Node&>);
    check(&tpy::builtin_max_elem<Node>(cv) == &v[1], "const vector: the element");
    check(&tpy::builtin_min_elem_key<Node>(cv, by_v) == &v[2], "const vector: keyed");

    std::array<Node, 3> a{{{3}, {9}, {0}}};
    static_assert(std::is_same_v<decltype(tpy::builtin_max_elem<Node>(a)), Node&>);
    check(&tpy::builtin_max_elem<Node>(a) == &a[1], "array: max is the element");
    check(&tpy::builtin_min_elem_key<Node>(a, by_v) == &a[2], "array: keyed min");

    std::span<Node> sp(v);
    static_assert(std::is_same_v<decltype(tpy::builtin_max_elem<Node>(sp)), Node&>);
    check(&tpy::builtin_max_elem<Node>(sp) == &v[1], "span: the viewed element");
    check(&tpy::builtin_min_elem_key<Node>(std::span<Node>(v), by_v) == &v[2],
          "span temporary: the viewed element");
    std::span<const Node> csp(v);
    static_assert(std::is_same_v<decltype(tpy::builtin_max_elem<Node>(csp)), const Node&>);
    static_assert(std::is_same_v<decltype(tpy::builtin_min_elem_key<Node>(csp, by_v)),
                                 const Node&>);
    check(&tpy::builtin_max_elem<Node>(csp) == &v[1], "const span: the viewed element");

    tpy::ordered_set<std::string> s;
    s.insert(std::string("m"));
    s.insert(std::string("z"));
    s.insert(std::string("a"));
    static_assert(std::is_same_v<decltype(tpy::builtin_max_elem<std::string>(s)),
                                 const std::string&>);
    const std::string& smax = tpy::builtin_max_elem<std::string>(s);
    check(smax == "z" && &smax == &*std::next(s.begin()), "set: the stored element");
    auto slen = [](const std::string& x) { return x; };
    check(&tpy::builtin_min_elem_key<std::string>(s, slen) == &*std::next(s.begin(), 2),
          "set: keyed min is the stored element");

    tpy::ordered_map<std::string, int32_t> d;
    d["k"] = 1;
    d["b"] = 2;
    static_assert(std::is_same_v<decltype(tpy::builtin_min_elem<std::string>(d)),
                                 const std::string&>);
    const std::string& dmin = tpy::builtin_min_elem<std::string>(d);
    check(dmin == "b" && &dmin == &*std::next(d.begin()), "dict: the stored key");
    check(&tpy::builtin_max_elem_key<std::string>(d, slen) == &*d.begin(),
          "dict: keyed max is the stored key");
}

// --- 2. iterator sources --------------------------------------------------------

// A generator-style self-iterator lending elements it does not own.
template<typename E>
struct Lender : tpy::next_iter_mixin<Lender<E>, tpy::val_or_ref<E>> {
    std::vector<Node>* src;
    size_t i = 0;
    explicit Lender(std::vector<Node>* s) : src(s) {}
    std::expected<tpy::val_or_ref<E>, tpy::StopIteration> __next__() {
        if (i >= src->size()) return tpy::make_unexpected(tpy::StopIteration{});
        return tpy::val_or_ref<E>((*src)[i++]);
    }
};

// A record whose `__iter__` returns a separate iterator over its storage.
struct Nodes {
    std::vector<Node> items{{4}, {8}, {8}, {1}};
    auto __iter__() { return ::tpy::__iter__(items); }
};

// A separate iterator that OWNS what it lends: each element lives in the
// iterator itself (as a generator frame's local does), so it dies with the
// iterator, inside the helper.
struct OwningIter : tpy::next_iter_mixin<OwningIter, tpy::val_or_ref<Node>> {
    std::vector<Node> held;
    size_t i = 0;
    explicit OwningIter(std::vector<Node> v) : held(std::move(v)) {}
    std::expected<tpy::val_or_ref<Node>, tpy::StopIteration> __next__() {
        if (i >= held.size()) return tpy::make_unexpected(tpy::StopIteration{});
        return tpy::val_or_ref<Node>(held[i++]);
    }
};

struct MadeOnDemand {
    std::vector<Node> seed{{3, 1}, {9, 2}, {5, 3}};
    OwningIter __iter__() { return OwningIter(seed); }
};

// A generator-like source that REBINDS the slot it yields (`p = P(v); yield p`
// in a loop): each step overwrites the element the previous one handed out.
struct Rebinder : tpy::next_iter_mixin<Rebinder, tpy::val_or_ref<Node>> {
    std::vector<int32_t> vals;
    size_t i = 0;
    Node slot{0};
    explicit Rebinder(std::vector<int32_t> v) : vals(std::move(v)) {}
    std::expected<tpy::val_or_ref<Node>, tpy::StopIteration> __next__() {
        if (i >= vals.size()) return tpy::make_unexpected(tpy::StopIteration{});
        slot = Node{vals[i], static_cast<int32_t>(i)};
        ++i;
        return tpy::val_or_ref<Node>(slot);
    }
};

// A record whose `__iter__` returns a reference to an iterator it stores.
struct HoldsIter {
    Rebinder inner{{4, 7, 2}};
    Rebinder& __iter__() { return inner; }
};

// An iterator that can be neither copied nor moved, returned by value from
// `__iter__` (guaranteed elision builds it in place).
struct Immovable : tpy::next_iter_mixin<Immovable, tpy::val_or_ref<Node>> {
    std::vector<Node>* src;
    size_t i = 0;
    explicit Immovable(std::vector<Node>* s) : src(s) {}
    Immovable(const Immovable&) = delete;
    Immovable(Immovable&&) = delete;
    std::expected<tpy::val_or_ref<Node>, tpy::StopIteration> __next__() {
        if (i >= src->size()) return tpy::make_unexpected(tpy::StopIteration{});
        return tpy::val_or_ref<Node>((*src)[i++]);
    }
};

struct MakesImmovable {
    std::vector<Node> items{{1}, {6}, {3}};
    Immovable __iter__() { return Immovable(&items); }
};

// Every source walked through `__next__` hands back the winner BY VALUE, a
// copy kept while walking: a step is valid only until the next one.
void iterator_sources() {
    std::vector<Node> v{{6}, {2}, {9}, {9}, {2}};
    Lender<Node> it(&v);
    static_assert(std::is_same_v<decltype(tpy::builtin_max_elem<Node>(it)), Node>);
    Node mx = tpy::builtin_max_elem<Node>(it);
    check(mx.v == 9, "self-iterator: max by value");
    Lender<Node> it2(&v);
    check(tpy::builtin_min_elem_key<Node>(it2, by_v).v == 2,
          "self-iterator: keyed min by value");
    check(tpy::builtin_max_elem_key<Node>(Lender<Node>(&v), by_v).v == 9,
          "self-iterator temporary: keyed max by value");
    Lender<const Node> cit(&v);
    static_assert(std::is_same_v<decltype(tpy::builtin_max_elem<Node>(cit)), Node>);
    check(tpy::builtin_min_elem<Node>(cit).v == 2, "const-lending iterator: by value");

    // The rebinding generator: the right winner, not the last step's slot.
    Rebinder r1({3, 9, 4});
    Node rmax = tpy::builtin_max_elem(r1);
    check(rmax.v == 9 && rmax.tag == 1, "rebinding source: max is the right element");
    Rebinder r2({3, 9, 4});
    Node rmin = tpy::builtin_min_elem_key(r2, by_v);
    check(rmin.v == 3 && rmin.tag == 0, "rebinding source: keyed min is the right element");
    check(tpy::builtin_max_elem_key(Rebinder({5, 1, 8}), by_v).tag == 2,
          "rebinding source temporary: keyed max is the right element");

    // A separate iterator, owned by the helper or stored by the source.
    Nodes ns;
    static_assert(std::is_same_v<decltype(tpy::builtin_max_elem<Node>(ns)), Node>);
    check(tpy::builtin_max_elem<Node>(ns).v == 8, "__iter__ record: max by value");
    check(tpy::builtin_min_elem_key<Node>(ns, by_v).v == 1,
          "__iter__ record: keyed min by value");
    MadeOnDemand md;
    static_assert(std::is_same_v<decltype(tpy::builtin_max_elem(md)), Node>);
    Node top = tpy::builtin_max_elem(md);
    check(top.v == 9 && top.tag == 2, "iterator-owned element: the value survives");
    Node low = tpy::builtin_min_elem_key(md, by_v);
    check(low.v == 3 && low.tag == 1, "iterator-owned element: keyed, the value survives");
    HoldsIter hi;
    Node hmax = tpy::builtin_max_elem(hi);
    check(hmax.v == 7 && hmax.tag == 1, "__iter__ returning a stored iterator: by value");
    MakesImmovable mi;
    check(tpy::builtin_max_elem_key(mi, by_v).v == 6, "immovable iterator: by value");

    // A native iterator over a container, named and temporary.
    auto vit = ::tpy::__iter__(v);
    check(tpy::builtin_max_elem(vit).v == 9, "native iterator lvalue: by value");
    check(tpy::builtin_min_elem_key(::tpy::__iter__(v), by_v).v == 2,
          "native iterator temporary: by value");

    // A dict's values: a container view, mutable and const, lends.
    tpy::ordered_map<std::string, Node> d;
    d["a"] = Node{4};
    d["b"] = Node{8};
    tpy::dict_values_view<std::string, Node> vals{&d};
    Node& dv = tpy::builtin_max_elem_key(vals, by_v);
    check(&dv == &d["b"], "dict values: the stored value");
    const auto& cd = d;
    tpy::dict_values_view<std::string, const Node> cvals{&cd};
    static_assert(std::is_same_v<decltype(tpy::builtin_min_elem_key(cvals, by_v)),
                                 const Node&>);
    check(&tpy::builtin_min_elem_key(cvals, by_v) == &d["a"], "const dict values: the stored value");

    // The deduced element type is the explicit one.
    static_assert(std::is_same_v<decltype(tpy::builtin_max_elem(v)), Node&>);
}

// --- 3. rvalue container --------------------------------------------------------

void rvalue_source() {
    static_assert(std::is_same_v<
        decltype(tpy::builtin_max_elem<Node>(std::vector<Node>{})), Node&>);
    Node copied = tpy::builtin_max_elem<Node>(std::vector<Node>{{1}, {3, 5}, {2}});
    check(copied.v == 3 && copied.tag == 5, "rvalue vector: copied within the expression");
    Node keyed = tpy::builtin_min_elem_key<Node>(std::vector<Node>{{4, 1}, {0, 2}}, by_v);
    check(keyed.tag == 2, "rvalue vector: keyed copy within the expression");
}

// --- 4. an element that can be neither copied nor moved --------------------------

struct Pinned {
    int32_t v;
    explicit Pinned(int32_t x) : v(x) {}
    Pinned(const Pinned&) = delete;
    Pinned(Pinned&&) = delete;
    bool operator<(const Pinned& o) const { return v < o.v; }
};

int32_t next_pinned = 0;
struct DefaultPinned {
    int32_t v;
    DefaultPinned() : v((next_pinned++ * 7) % 5) {}
    DefaultPinned(const DefaultPinned&) = delete;
    DefaultPinned(DefaultPinned&&) = delete;
    bool operator<(const DefaultPinned& o) const { return v < o.v; }
};

void pinned_elements() {
    std::array<Pinned, 3> a{Pinned(5), Pinned(8), Pinned(3)};
    check(&tpy::builtin_max_elem<Pinned>(a) == &a[1], "pinned array: max");
    check(&tpy::builtin_min_elem_key<Pinned>(a, [](const Pinned& p) { return p.v; })
              == &a[2], "pinned array: keyed min");
    next_pinned = 0;
    std::vector<DefaultPinned> v(4);  // values 0, 2, 4, 1
    check(&tpy::builtin_max_elem<DefaultPinned>(v) == &v[2], "pinned vector: max");
    check(&tpy::builtin_min_elem_key<DefaultPinned>(
              v, [](const DefaultPinned& p) { return p.v; }) == &v[0],
          "pinned vector: keyed min");
}

// --- 5. semantics -----------------------------------------------------------------

void ties_first_wins() {
    std::vector<Node> v{{1}, {7}, {7}, {1}};
    check(&tpy::builtin_max_elem<Node>(v) == &v[1], "max: first of equals");
    check(&tpy::builtin_min_elem<Node>(v) == &v[0], "min: first of equals");
    check(&tpy::builtin_max_elem_key<Node>(v, by_v) == &v[1], "keyed max: first of equals");
    check(&tpy::builtin_min_elem_key<Node>(v, by_v) == &v[0], "keyed min: first of equals");
    tpy::ordered_set<std::string> s;
    s.insert(std::string("bb"));
    s.insert(std::string("aa"));
    s.insert(std::string("c"));
    auto len = [](const std::string& x) { return x.size(); };
    check(&tpy::builtin_max_elem_key<std::string>(s, len) == &*s.begin(),
          "keyed max over a set: first of equals");
}

void key_once_in_order() {
    std::vector<Node> v{{3}, {1}, {2}};
    std::vector<int32_t> seen;
    auto rec = [&](const Node& n) { seen.push_back(n.v); return n.v; };
    tpy::builtin_max_elem_key<Node>(v, rec);
    check(seen == std::vector<int32_t>({3, 1, 2}), "list: key once per element, in order");
    seen.clear();
    Lender<Node> it(&v);
    (void)tpy::builtin_min_elem_key<Node>(it, rec);
    check(seen == std::vector<int32_t>({3, 1, 2}), "iterator: key once per element, in order");
}

// Any size change of a container the reference walks raises, growth too:
// a reference the walk holds would point at storage the change moved.
void size_change_raises() {
    int raised = 0;
    std::vector<Node> v{{3}, {1}, {2}};
    // Headroom: the growth leaves the storage in place, so the size check
    // alone is what catches it.
    v.reserve(16);
    bool once = false;
    auto grow = [&](const Node& n) {
        if (!once) { once = true; v.push_back(Node{10}); }
        return n.v;
    };
    try { (void)tpy::builtin_max_elem_key<Node>(v, grow); }
    catch (const tpy::RuntimeError&) { ++raised; }
    v.assign({{1}, {9}, {2}, {3}});
    auto shrink = [&](const Node& n) {
        if (n.v == 9) v.pop_back();
        return n.v;
    };
    try { (void)tpy::builtin_min_elem_key<Node>(v, shrink); }
    catch (const tpy::RuntimeError&) { ++raised; }
    // The comparison form: a user `__lt__` that resizes the source.
    struct Grower {
        int32_t v;
        std::vector<Grower>* owner;
        bool operator<(const Grower& o) const {
            if (owner->size() < 8) owner->push_back(Grower{0, owner});
            return v < o.v;
        }
    };
    std::vector<Grower> g;
    g.reserve(16);
    g.push_back(Grower{1, &g});
    g.push_back(Grower{2, &g});
    try { (void)tpy::builtin_max_elem(g); }
    catch (const tpy::RuntimeError&) { ++raised; }
    struct Shrinker {
        int32_t v;
        std::vector<Shrinker>* owner;
        bool operator<(const Shrinker& o) const {
            if (owner->size() > 2) owner->pop_back();
            return v < o.v;
        }
    };
    std::vector<Shrinker> k;
    k.reserve(16);
    k.push_back(Shrinker{1, &k});
    k.push_back(Shrinker{2, &k});
    k.push_back(Shrinker{3, &k});
    try { (void)tpy::builtin_min_elem(k); }
    catch (const tpy::RuntimeError&) { ++raised; }
    // A key TYPE whose `<` resizes the source: the comparison of two keys
    // is user code too.
    struct ResizingKey {
        int32_t k;
        std::vector<Node>* owner;
        bool operator<(const ResizingKey& o) const {
            if (owner->size() > 2) owner->pop_back();
            return k < o.k;
        }
    };
    std::vector<Node> rk{{1}, {2}, {3}};
    try {
        (void)tpy::builtin_max_elem_key<Node>(
            rk, [&](const Node& n) { return ResizingKey{n.v, &rk}; });
    } catch (const tpy::RuntimeError&) { ++raised; }
    // Grow past capacity and shrink back: the size is unchanged, the storage
    // the walk points into is not.
    std::vector<Node> gs{{3}, {1}, {2}};
    gs.shrink_to_fit();
    bool grown = false;
    auto regrow = [&](const Node& n) {
        // Read before the growth moves `n`.
        const int32_t v = n.v;
        if (!grown) {
            grown = true;
            for (int i = 0; i < 20; ++i) gs.push_back(Node{0});
            gs.resize(3);
        }
        return v;
    };
    try { (void)tpy::builtin_max_elem_key<Node>(gs, regrow); }
    catch (const tpy::RuntimeError&) { ++raised; }
    // A key `<` that drops the CURRENT element on the last comparison: only
    // the check after the comparison sees it before the walk would keep a
    // pointer to the dropped element.
    struct DropKey {
        int32_t k;
        std::vector<Node>* owner;
        bool operator<(const DropKey& o) const {
            if (owner->size() > 1) owner->pop_back();
            return k < o.k;
        }
    };
    std::vector<Node> dk{{1}, {2}};
    try {
        (void)tpy::builtin_max_elem_key<Node>(
            dk, [&](const Node& n) { return DropKey{n.v, &dk}; });
    } catch (const tpy::RuntimeError&) { ++raised; }
    check(raised == 7, "a size or storage change during the walk raises RuntimeError");
}

void borrowed_results() {
    std::vector<Node> v{{3}, {9}, {2}};
    check(&tpy::assert_lent(tpy::builtin_max_elem<Node>(v)) == &v[1],
          "borrow: a container's element");
    check(tpy::assert_lent(tpy::builtin_min_elem_key<Node>(
              std::vector<Node>{{4}, {1}}, by_v)).v == 1,
          "borrow: a temporary container's element");
    // An iterator's winner is a value, which only a non-borrow call takes.
    Lender<Node> it(&v);
    static_assert(std::is_same_v<decltype(tpy::builtin_max_elem<Node>(it)), Node>);
    check(tpy::builtin_max_elem<Node>(it).v == 9,
          "an iterator's winner by value");
}

void empty_raises() {
    std::vector<Node> v;
    int raised = 0;
    try { tpy::builtin_min_elem<Node>(v); } catch (const tpy::ValueError&) { ++raised; }
    try { tpy::builtin_max_elem_key<Node>(v, by_v); } catch (const tpy::ValueError&) { ++raised; }
    Lender<Node> it(&v);
    try { (void)tpy::builtin_max_elem<Node>(it); } catch (const tpy::ValueError&) { ++raised; }
    check(raised == 3, "an empty source raises ValueError");
}

}  // namespace

int main() {
    containers();
    borrowed_results();
    iterator_sources();
    rvalue_source();
    pinned_elements();
    ties_first_wins();
    key_once_in_order();
    size_change_raises();
    empty_raises();
    if (failures == 0) std::printf("ok\n");
    return failures == 0 ? 0 : 1;
}
