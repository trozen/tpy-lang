/**
 * TurboPython Runtime - Ordered Map
 *
 * Hash map that preserves insertion order (matching Python 3.7+ dict semantics).
 * Combines std::unordered_map for O(1) lookup with an intrusive doubly-linked
 * list for insertion-order iteration.
 *
 * Default iteration yields keys only (matching Python's `for k in d`).
 * Use items_begin()/items_end() for key-value pair iteration.
 */

#pragma once

#include <cstdint>
#include <expected>
#include <functional>
#include <initializer_list>
#include <tuple>
#include <unordered_map>
#include <utility>

#include "core.hpp"

namespace tpy {

template<typename K, typename V>
class ordered_map {
    struct Node {
        K key;
        V value;
        Node* prev = nullptr;
        Node* next = nullptr;

        template<typename KK, typename VV>
        Node(KK&& k, VV&& v)
            : key(std::forward<KK>(k)), value(std::forward<VV>(v)) {}
    };

public:
    // -- Key iterator (default -- matches Python's `for k in d`) ------------

    template<bool IsConst>
    class key_iterator_impl {
        friend class ordered_map;
        using NodePtr = std::conditional_t<IsConst, const Node*, Node*>;
        NodePtr node_;

        explicit key_iterator_impl(NodePtr n) : node_(n) {}

    public:
        // Required by std::forward_iterator (std::default_initializable)
        key_iterator_impl() : node_(nullptr) {}

        using iterator_category = std::forward_iterator_tag;
        using difference_type = std::ptrdiff_t;
        using value_type = K;
        using reference = const K&;

        const K& operator*() const { return node_->key; }

        key_iterator_impl& operator++() { node_ = node_->next; return *this; }
        key_iterator_impl operator++(int) { auto tmp = *this; node_ = node_->next; return tmp; }

        bool operator==(const key_iterator_impl& o) const { return node_ == o.node_; }
        bool operator!=(const key_iterator_impl& o) const { return node_ != o.node_; }
    };

    // -- Items iterator (for .items() / internal use) -----------------------

    template<bool IsConst>
    class items_iterator_impl {
        friend class ordered_map;
        using NodePtr = std::conditional_t<IsConst, const Node*, Node*>;
        NodePtr node_;

        explicit items_iterator_impl(NodePtr n) : node_(n) {}

    public:
        using iterator_category = std::forward_iterator_tag;
        using difference_type = std::ptrdiff_t;
        // Proxy pair: references into Node data, returned by value.
        // Safe for range-for with auto&& structured bindings.
        using value_type = std::pair<const K&,
            std::conditional_t<IsConst, const V&, V&>>;

        value_type operator*() const { return {node_->key, node_->value}; }

        items_iterator_impl& operator++() { node_ = node_->next; return *this; }
        items_iterator_impl operator++(int) { auto tmp = *this; node_ = node_->next; return tmp; }

        bool operator==(const items_iterator_impl& o) const { return node_ == o.node_; }
        bool operator!=(const items_iterator_impl& o) const { return node_ != o.node_; }
    };

    // -- Value iterator (for .values()) -------------------------------------

    template<bool IsConst>
    class value_iterator_impl {
        friend class ordered_map;
        using NodePtr = std::conditional_t<IsConst, const Node*, Node*>;
        NodePtr node_;

        explicit value_iterator_impl(NodePtr n) : node_(n) {}

    public:
        // Required by std::forward_iterator (std::default_initializable)
        value_iterator_impl() : node_(nullptr) {}

        using iterator_category = std::forward_iterator_tag;
        using difference_type = std::ptrdiff_t;
        using value_type = V;
        using reference = std::conditional_t<IsConst, const V&, V&>;

        reference operator*() const { return node_->value; }

        value_iterator_impl& operator++() { node_ = node_->next; return *this; }
        value_iterator_impl operator++(int) { auto tmp = *this; node_ = node_->next; return tmp; }

        bool operator==(const value_iterator_impl& o) const { return node_ == o.node_; }
        bool operator!=(const value_iterator_impl& o) const { return node_ != o.node_; }
    };

    // -- Tuple-items iterator (yields tuples of refs for tuple unpacking) ---

    template<bool IsConst>
    class tuple_items_iterator_impl {
        friend class ordered_map;
        using NodePtr = std::conditional_t<IsConst, const Node*, Node*>;
        NodePtr node_;

        explicit tuple_items_iterator_impl(NodePtr n) : node_(n) {}

    public:
        // Required by std::forward_iterator (std::default_initializable)
        tuple_items_iterator_impl() : node_(nullptr) {}

        using iterator_category = std::forward_iterator_tag;
        using difference_type = std::ptrdiff_t;
        // Proxy reference tuple (C++23 zip_view shape): elements reference
        // the node so consumers alias the stored value (CPython `for k, v
        // in d.items()` mutation semantics) instead of receiving a copy.
        using value_type = std::tuple<K, V>;
        using reference = std::tuple<const K&,
            std::conditional_t<IsConst, const V&, V&>>;

        reference operator*() const { return {node_->key, node_->value}; }

        tuple_items_iterator_impl& operator++() { node_ = node_->next; return *this; }
        tuple_items_iterator_impl operator++(int) { auto tmp = *this; node_ = node_->next; return tmp; }

        bool operator==(const tuple_items_iterator_impl& o) const { return node_ == o.node_; }
        bool operator!=(const tuple_items_iterator_impl& o) const { return node_ != o.node_; }
    };

    using iterator = key_iterator_impl<false>;
    using const_iterator = key_iterator_impl<true>;
    using items_iterator = items_iterator_impl<false>;
    using const_items_iterator = items_iterator_impl<true>;
    using value_iterator = value_iterator_impl<false>;
    using const_value_iterator = value_iterator_impl<true>;
    using tuple_items_iterator = tuple_items_iterator_impl<false>;
    using const_tuple_items_iterator = tuple_items_iterator_impl<true>;

    // -- Construction -------------------------------------------------------

    ordered_map() = default;

    ordered_map(std::initializer_list<std::tuple<K, V>> init) {
        for (auto& t : init) {
            insert_or_assign(std::get<0>(t), std::get<1>(t));
        }
    }

    ordered_map(const ordered_map& other) {
        for (auto* n = other.head_; n != nullptr; n = n->next) {
            insert_or_assign(n->key, n->value);
        }
    }

    ordered_map(ordered_map&& other) noexcept
        : table_(std::move(other.table_))
        , head_(other.head_)
        , tail_(other.tail_)
    {
        other.head_ = nullptr;
        other.tail_ = nullptr;
    }

    ordered_map& operator=(const ordered_map& other) {
        if (this != &other) {
            clear();
            for (auto* n = other.head_; n != nullptr; n = n->next) {
                insert_or_assign(n->key, n->value);
            }
        }
        return *this;
    }

    ordered_map& operator=(ordered_map&& other) noexcept {
        if (this != &other) {
            clear();
            table_ = std::move(other.table_);
            head_ = other.head_;
            tail_ = other.tail_;
            other.head_ = nullptr;
            other.tail_ = nullptr;
        }
        return *this;
    }

    ~ordered_map() {
        clear();
    }

    // -- Element access -----------------------------------------------------

    // Inserts V{} on missing key (std::map semantics, NOT Python semantics).
    // Not used by generated code; provided for standalone C++ use.
    V& operator[](const K& key) {
        auto it = table_.find(key);
        if (it != table_.end()) {
            return it->second->value;
        }
        auto* node = new Node(key, V{});
        link_back(node);
        table_.emplace(key, node);
        return node->value;
    }

    // -- Capacity -----------------------------------------------------------

    int32_t size() const { return static_cast<int32_t>(table_.size()); }
    bool empty() const { return table_.empty(); }

    // -- Modifiers ----------------------------------------------------------

    template<typename KK, typename VV>
    void insert_or_assign(KK&& key, VV&& value) {
        auto it = table_.find(key);
        if (it != table_.end()) {
            it->second->value = std::forward<VV>(value);
            return;
        }
        auto* node = new Node(std::forward<KK>(key), std::forward<VV>(value));
        link_back(node);
        table_.emplace(node->key, node);
    }

    bool erase(const K& key) {
        auto it = table_.find(key);
        if (it == table_.end()) return false;
        Node* node = it->second;
        table_.erase(it);
        unlink(node);
        delete node;
        return true;
    }

    void erase(items_iterator pos) {
        Node* node = pos.node_;
        table_.erase(node->key);
        unlink(node);
        delete node;
    }

    void clear() {
        auto* n = head_;
        while (n != nullptr) {
            auto* next = n->next;
            delete n;
            n = next;
        }
        table_.clear();
        head_ = nullptr;
        tail_ = nullptr;
    }

    // -- Lookup -------------------------------------------------------------

    bool contains(const K& key) const {
        return table_.find(key) != table_.end();
    }

    items_iterator find(const K& key) {
        auto it = table_.find(key);
        if (it == table_.end()) return items_end();
        return items_iterator(it->second);
    }

    const_items_iterator find(const K& key) const {
        auto it = table_.find(key);
        if (it == table_.end()) return items_end();
        return const_items_iterator(it->second);
    }

    // -- Iteration (insertion order) ----------------------------------------

    // Default: iterate keys (matches Python's `for k in d`)
    iterator begin() { return iterator(head_); }
    iterator end() { return iterator(nullptr); }
    const_iterator begin() const { return const_iterator(head_); }
    const_iterator end() const { return const_iterator(nullptr); }

    // Items: iterate key-value pairs (for .items() and internal use)
    items_iterator items_begin() { return items_iterator(head_); }
    items_iterator items_end() { return items_iterator(nullptr); }
    const_items_iterator items_begin() const { return const_items_iterator(head_); }
    const_items_iterator items_end() const { return const_items_iterator(nullptr); }

    // Values: iterate values only (for .values())
    value_iterator values_begin() { return value_iterator(head_); }
    value_iterator values_end() { return value_iterator(nullptr); }
    const_value_iterator values_begin() const { return const_value_iterator(head_); }
    const_value_iterator values_end() const { return const_value_iterator(nullptr); }

    // Tuple-items: iterate as ref tuples (for Python-style `for k, v in d.items()`)
    tuple_items_iterator tuple_items_begin() { return tuple_items_iterator(head_); }
    tuple_items_iterator tuple_items_end() { return tuple_items_iterator(nullptr); }
    const_tuple_items_iterator tuple_items_begin() const { return const_tuple_items_iterator(head_); }
    const_tuple_items_iterator tuple_items_end() const { return const_tuple_items_iterator(nullptr); }

    // -- Python __iter__() support (returns native_iterator for Iterable[K]) -

    // Defined in dict_ops.hpp (needs native_iterator template)
    inline auto __iter__() const;

    // -- Comparison (order-independent, matching Python) --------------------

    bool operator==(const ordered_map& other) const {
        if (table_.size() != other.table_.size()) return false;
        for (auto* n = head_; n != nullptr; n = n->next) {
            auto it = other.table_.find(n->key);
            if (it == other.table_.end()) return false;
            if (!(n->value == it->second->value)) return false;
        }
        return true;
    }

    bool operator!=(const ordered_map& other) const {
        return !(*this == other);
    }

private:
    void link_back(Node* node) {
        node->prev = tail_;
        node->next = nullptr;
        if (tail_) {
            tail_->next = node;
        } else {
            head_ = node;
        }
        tail_ = node;
    }

    void unlink(Node* node) {
        if (node->prev) {
            node->prev->next = node->next;
        } else {
            head_ = node->next;
        }
        if (node->next) {
            node->next->prev = node->prev;
        } else {
            tail_ = node->prev;
        }
    }

    template<typename, typename> friend struct OwnIterDict;
    template<typename, typename> friend struct OwnIterDictItems;

    std::unordered_map<K, Node*> table_;
    Node* head_ = nullptr;
    Node* tail_ = nullptr;
};

namespace detail {

template<typename K, typename V>
inline void make_ordered_map_impl(ordered_map<K, V>&) {}

template<typename K, typename V, typename KK, typename VV, typename... Rest>
void make_ordered_map_impl(ordered_map<K, V>& m, KK&& k, VV&& v, Rest&&... rest) {
    m.insert_or_assign(std::forward<KK>(k), std::forward<VV>(v));
    make_ordered_map_impl<K, V>(m, std::forward<Rest>(rest)...);
}

} // namespace detail

// Construct an ordered_map from move-only K or V types. Mirrors make_vector:
// the initializer_list ctor stores elements as const, forcing a copy of V --
// this helper forwards each pair into insert_or_assign so move-only values
// (e.g. Rc[T], Box[T]) and keys work in dict literals.
template<typename K, typename V, typename... Args>
ordered_map<K, V> make_ordered_map(Args&&... args) {
    static_assert(sizeof...(Args) % 2 == 0,
                  "make_ordered_map requires an even number of arguments (key/value pairs)");
    ordered_map<K, V> m;
    detail::make_ordered_map_impl<K, V>(m, std::forward<Args>(args)...);
    return m;
}

// ---------------------------------------------------------------------------
// OwnIterDict -- drain iterator for ordered_map keys
//
// Owns a moved ordered_map and yields keys by move.
// ---------------------------------------------------------------------------

template<typename K, typename V>
struct OwnIterDict {
    ordered_map<K, V> data;
    typename ordered_map<K, V>::Node* pos;

    explicit OwnIterDict(ordered_map<K, V>&& m)
        : data(std::move(m)), pos(data.head_) {}

    OwnIterDict(const OwnIterDict&) = delete;
    OwnIterDict& operator=(const OwnIterDict&) = delete;
    OwnIterDict(OwnIterDict&&) = default;
    OwnIterDict& operator=(OwnIterDict&&) = default;

    struct move_iter {
        using Node = typename ordered_map<K, V>::Node;
        Node* node_;

        using iterator_category = std::input_iterator_tag;
        using value_type = K;
        using difference_type = std::ptrdiff_t;
        using reference = K&&;

        move_iter() : node_(nullptr) {}
        explicit move_iter(Node* n) : node_(n) {}

        K&& operator*() { return std::move(node_->key); }
        move_iter& operator++() { node_ = node_->next; return *this; }
        move_iter operator++(int) { auto tmp = *this; ++*this; return tmp; }
        bool operator==(const move_iter& o) const { return node_ == o.node_; }
        bool operator!=(const move_iter& o) const { return node_ != o.node_; }
    };

    // Do not mix range-based (begin/end) and __next__-based iteration on the
    // same instance -- both advance shared state and the result is undefined.
    move_iter begin() { return move_iter(pos); }
    move_iter end()   { return move_iter(nullptr); }

    std::expected<K, StopIteration> __next__() {
        if (pos == nullptr) return tpy::make_unexpected(StopIteration{});
        K key = std::move(pos->key);
        pos = pos->next;
        return key;
    }

    OwnIterDict& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const OwnIterDict&) {
        return os << "<own_iter_dict>";
    }
};

template<typename K, typename V>
OwnIterDict<K, V> own_iter_dict(ordered_map<K, V>&& m) {
    return OwnIterDict<K, V>{std::move(m)};
}

// ---------------------------------------------------------------------------
// OwnIterDictItems -- drain iterator for ordered_map items (key-value pairs)
//
// Owns a moved ordered_map and yields std::tuple<K, V> by move.
// ---------------------------------------------------------------------------

template<typename K, typename V>
struct OwnIterDictItems {
    ordered_map<K, V> data;
    typename ordered_map<K, V>::Node* pos;

    explicit OwnIterDictItems(ordered_map<K, V>&& m)
        : data(std::move(m)), pos(data.head_) {}

    OwnIterDictItems(const OwnIterDictItems&) = delete;
    OwnIterDictItems& operator=(const OwnIterDictItems&) = delete;
    OwnIterDictItems(OwnIterDictItems&&) = default;
    OwnIterDictItems& operator=(OwnIterDictItems&&) = default;

    struct move_iter {
        using Node = typename ordered_map<K, V>::Node;
        Node* node_;

        using iterator_category = std::input_iterator_tag;
        using value_type = std::tuple<K, V>;
        using difference_type = std::ptrdiff_t;
        using reference = std::tuple<K, V>;

        move_iter() : node_(nullptr) {}
        explicit move_iter(Node* n) : node_(n) {}

        std::tuple<K, V> operator*() {
            return std::tuple<K, V>(std::move(node_->key), std::move(node_->value));
        }
        move_iter& operator++() { node_ = node_->next; return *this; }
        move_iter operator++(int) { auto tmp = *this; ++*this; return tmp; }
        bool operator==(const move_iter& o) const { return node_ == o.node_; }
        bool operator!=(const move_iter& o) const { return node_ != o.node_; }
    };

    // Do not mix range-based (begin/end) and __next__-based iteration on the
    // same instance -- both advance shared state and the result is undefined.
    move_iter begin() { return move_iter(pos); }
    move_iter end()   { return move_iter(nullptr); }

    std::expected<std::tuple<K, V>, StopIteration> __next__() {
        if (pos == nullptr) return tpy::make_unexpected(StopIteration{});
        auto item = std::tuple<K, V>(std::move(pos->key), std::move(pos->value));
        pos = pos->next;
        return item;
    }

    OwnIterDictItems& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const OwnIterDictItems&) {
        return os << "<own_iter_dict_items>";
    }
};

template<typename K, typename V>
OwnIterDictItems<K, V> own_iter_dict_items(ordered_map<K, V>&& m) {
    return OwnIterDictItems<K, V>{std::move(m)};
}

}  // namespace tpy
