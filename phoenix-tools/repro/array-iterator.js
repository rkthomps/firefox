// A for-of over an array calls the self-hosted intrinsic NewArrayIterator,
// with no arguments. Its Call stub (NewArrayIteratorResult) mentions none of
// its operands, not even its input operand argc.
let n = 0;
for (let i = 0; i < 20; i++) {
  for (const x of [1, 2, 3]) {
    n += x;
  }
}
print(n);
