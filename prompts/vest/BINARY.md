
### Fixed-size unsigned integers
`u8`, `u16`, `u24`, `u32`, `u64`

### Integers with constraints
```
u16 | 8..0xFFFE
```

### Constant byte-arrays
```
const a: [u8; 48] = [0; 48]
```
### Fixed-length byte-arrays
```
[u8; N]
```

### Variable-length byte-arrays
```
b(@len: u24) = [u8; @len]
```

### Struct with dependent fields
```
c = { @x: u24, y: b(@x) }
```

### Count-bounded repetitions
```
d = (@cnt: u64) = [a; @cnt]
```

### Length-bounded repetitions
```
e = { @len: u8, v: [u8; @len] >>= Vec<d> }
```

### Optional values
```
g = Option<[u8; 32]>
```

### Finite enumerations
```
f = enum { A = 1, B = 2}
```

### Explicitly tagged unions
```
i = { @tag: f, y: h(@tag) }
```

### Implicitly tagged unions
```
h(@flag: f) = choose(@flag) {
  A => c,
  B => e
}
```

### Untagged unions
```
msg = choose {
  A(u8 | ..0xFE),
  B(u8 | 0xFF)
}
```