// Run with --setpref=experimental.arraybuffer_immutable=true. Attaches stubs whose CacheIR
// has an ArrayBufferViewKind::Immutable argument.
const ta = new Int32Array(new ArrayBuffer(16).transferToImmutable());
let sum = 0;
for (let i = 0; i < 100; i++) {
  sum += ta[i & 3] + ta.length;
}
print(sum);
