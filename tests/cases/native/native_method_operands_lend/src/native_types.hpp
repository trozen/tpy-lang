#pragma once
#include <cstdint>
#include <optional>
#include <tuple>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

struct Node { int32_t v; };

// `pick` hands back its ARGUMENT by reference: what the stub, declaring no
// result=, must assume of every operand.
struct Picker {
    Node own{1};
    Node& pick(Node& other) { return other; }
    // A temporary argument is handed back too: the result dangles once
    // the statement ends.
    Node& pick(Node&& other) { return static_cast<Node&>(other); }
};

// A free binding returning its argument by reference, as the TPy twin
// `def first_of(n: Node) -> Node: return n` does.
inline Node& first_of(Node& n) { return n; }

// `bump` writes its argument; `peek`, `@readonly` like its TPy twin, takes
// its receiver and its argument const.
struct Bumper {
    void bump(Node& n) { n.v += 1; }
    int32_t peek(const Node& n) const { return n.v; }
};

inline int32_t text_len(std::string_view s) { return static_cast<int32_t>(s.size()); }

// Statics renamed on the TPy side.
struct Maker {
    static Node build(int32_t v) { return Node{v}; }
};

// A result pointing through a `Ptr` operand, and a view into a `String`.
inline Node& through(Node* p) { return *p; }
inline std::string_view view_of(const std::string& s) { return s; }

struct Holder {
    static Node& first(Node& n) { return n; }
};

// The method forms of `through` / `view_of`.
struct Via {
    Node& through(Node* p) { return *p; }
    std::string_view view_of(const std::string& s) { return s; }
};

// A generic binding handing back its argument, and an opaque registry
// whose lookup points into it.
template <class T>
T& gfirst(T& n) { return n; }
template <class T>
T& gfirst(T&& n) { return static_cast<T&>(n); }

struct Reg { int32_t tag; Node n{41}; };
inline Node& lookup(Reg& r) { return r.n; }
inline Node& lookup(Reg&& r) { return static_cast<Reg&>(r).n; }

// The generic binding `same` renders to: its argument, by reference.
template <class T>
T& identity(T& x) { return x; }

// Lending-reach shapes: a method handing back a subclass argument as its base, a
// pointer into an opaque object, and a lookup whose key is only compared.
struct SubPicker {
    template <class D>
    std::remove_reference_t<D>& pick1(D&& x) {
        return static_cast<std::remove_reference_t<D>&>(x);
    }
};

struct Opaque {
    int32_t value = 42;
    int32_t* data() { return &value; }
};

struct Finder {
    Node n{5};
    template <class K>
    Node& find(const K&) { return n; }
};

// Tuple results holding their class argument by pointer: inside an
// Optional, and beside a number while a `str` argument is only read.
template <class B>
std::optional<std::tuple<B*, int32_t>> opt_pair(B& b) {
    return std::tuple<B*, int32_t>{&b, 1};
}

template <class B>
std::tuple<B*, int32_t> pair_with(B& b, std::string_view s) {
    return {&b, static_cast<int32_t>(s.size())};
}
