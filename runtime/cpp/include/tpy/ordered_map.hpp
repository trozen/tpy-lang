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
#include <functional>
#include <initializer_list>
#include <tuple>
#include <unordered_map>
#include <utility>

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

    // -- Tuple-items iterator (yields std::tuple<K,V> for tuple unpacking) ---

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
        using value_type = std::tuple<K, V>;
        using reference = value_type;

        value_type operator*() const { return {node_->key, node_->value}; }

        tuple_items_iterator_impl& operator++() { node_ = node_->next; return *this; }
        tuple_items_iterator_impl operator++(int) { auto tmp = *this; node_ = node_->next; return tmp; }

        bool operator==(const tuple_items_iterator_impl& o) const { return node_ == o.node_; }
        bool operator!=(const tuple_items_iterator_impl& o) const { return node_ != o.node_; }
    };

    using iterator = key_iterator_impl<false>;
    using const_iterator = key_iterator_impl<true>;
    using items_iterator = items_iterator_impl<false>;
    using const_items_iterator = items_iterator_impl<true>;
    using const_value_iterator = value_iterator_impl<true>;
    using const_tuple_items_iterator = tuple_items_iterator_impl<true>;

    // -- Construction -------------------------------------------------------

    ordered_map() = default;

    ordered_map(std::initializer_list<std::pair<K, V>> init) {
        for (auto& [k, v] : init) {
            insert_or_assign(k, v);
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

    void insert_or_assign(const K& key, V value) {
        auto it = table_.find(key);
        if (it != table_.end()) {
            it->second->value = std::move(value);
            return;
        }
        auto* node = new Node(key, std::move(value));
        link_back(node);
        table_.emplace(key, node);
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
    const_value_iterator values_begin() const { return const_value_iterator(head_); }
    const_value_iterator values_end() const { return const_value_iterator(nullptr); }

    // Tuple-items: iterate as std::tuple<K,V> (for Python-style `for k, v in d.items()`)
    const_tuple_items_iterator tuple_items_begin() const { return const_tuple_items_iterator(head_); }
    const_tuple_items_iterator tuple_items_end() const { return const_tuple_items_iterator(nullptr); }

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

    std::unordered_map<K, Node*> table_;
    Node* head_ = nullptr;
    Node* tail_ = nullptr;
};

}  // namespace tpy
