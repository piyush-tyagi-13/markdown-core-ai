# Rust Ownership and Borrowing

Rust enforces memory safety at compile time through ownership. Each value has a
single owner, and the value is dropped when the owner goes out of scope. You can
borrow a reference instead of moving ownership. The borrow checker allows many
shared immutable references or exactly one mutable reference, never both at once.
Lifetimes annotate how long references stay valid so the compiler rejects dangling
pointers. This eliminates use-after-free bugs without a garbage collector.
