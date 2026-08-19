# Grammar specification language: Vest
**Only use the Vest syntax explained in this document. It is STRICTLY prohibited to use syntax not declared here.**
**The output MUST be one valid `.vest` file. Do not output explanations, markdown fences, Rust code, or text outside the `.vest` file.**
**The main parsing format MUST be an unparameterized struct named exactly `message`. This definition is mandatory and must occur exactly once.**

## General guidelines
1. Vest is a binary format description language that generates parsers and serializers. A Vest file contains named format definitions. Use one named format for each meaningful protocol structure, such as a header, payload, option, record, or tagged alternative.
2. The root protocol data unit must always be declared as `message = { ... }`. Do not call it `packet`, `frame`, `msg`, or any other name. `message` must not take parameters because it is the parser entry point.
3. Define helper formats globally and use them from `message`. Prefer declaring helpers before the formats that use them, and place `message` last.
4. Describe the wire format exactly in parsing order. Vest does not add implicit padding or alignment. Represent every padding or reserved byte explicitly.
5. Give every semantically meaningful parsed field its own named struct field. Do not merge adjacent fields merely because their combined size matches an integer type.
6. Use dependent fields and parameters when a previously parsed length, count, tag, or nested header field controls a later format.
7. Validate constants, legal integer ranges, enum values, lengths, and tag-dependent layouts whenever the specification provides enough information.
8. Do not invent protocol fields, magic values, delimiters, checksums, padding, defaults, or recovery rules that are not in the specification.
9. Vest parses one format from the start of the input. The caller requires `message` to consume the complete input, so ensure all bytes belonging to the protocol data unit are modeled. Use `Tail` only when the specification truly defines the remainder as opaque data.
10. Use lowercase `snake_case` for format and field names, uppercase `SCREAMING_SNAKE_CASE` for named constants, and descriptive `UpperCamelCase` labels for enum and choice variants.

## File and declaration syntax
* A normal named format has the form `name = FORMAT`.
* A parameterized helper has the form `name(@parameter: FORMAT) = FORMAT`.
* A named constant format has the form `const NAME: FORMAT = VALUE`.
* Definitions do not end in semicolons.
* Struct fields, enum variants, and choice branches must each end with a comma, including the final item.
* Line comments start with `//`. Do not use `#` comments or block comments.
* Vest definitions are not Rust declarations. Do not emit `struct`, `type`, `fn`, `pub`, `use`, `mod`, `impl`, or Rust attributes.
* Identifiers use ASCII letters, digits, and underscores and must not start with a digit. Do not use Vest keywords such as `const`, `enum`, `choose`, `Option`, `Vec`, `Tail`, `wrap`, `u8`, `u16`, `u24`, `u32`, or `u64` as format or field names.

A typical file has this shape:
```vest
!BIG_ENDIAN

kind = enum {
  Data = 1,
  Ack = 2,
}

data_payload(@length: u16) = {
  bytes: [u8; @length],
}

payload(@kind: kind, @length: u16) = choose(@kind) {
  Data => data_payload(@length),
  Ack => [u8; @length],
}

message = {
  @kind: kind,
  @payload_length: u16,
  payload: payload(@kind, @payload_length),
}
```

## Byte order
Vest uses one byte order for all multi-byte integers in a file.

* Little-endian is the default. Use `!LITTLE_ENDIAN` when stating it explicitly improves clarity.
* Use `!BIG_ENDIAN` for network byte order or whenever the specification says multi-byte integers are big-endian.
* Put at most one endianness directive in the file, normally as the first non-comment line.
* Endianness does not affect `u8`.
* The syntax in this document has no per-field byte-order override. Do not invent suffixes such as `u16be` or attributes such as `@big_endian`. If a format genuinely mixes byte orders, do not falsely claim that a single directive models both.

Example:
```vest
!BIG_ENDIAN

message = {
  version: u8,
  payload_length: u16,
  sequence_number: u32,
}
```

## Integer formats

### Fixed-size unsigned integers
Use only these fixed-size unsigned integer formats:
```vest
u8
u16
u24
u32
u64
```
Their widths are 1, 2, 3, 4, and 8 bytes respectively. `u24` represents a three-byte unsigned integer. The file-wide byte order applies to all multi-byte integer formats.

### Integer constants
An inline constant integer parses the integer and requires an exact value:
```vest
message = {
  const version: u8 = 1,
  const marker: u16 = 0xCAFE,
}
```
Use a top-level named constant when the same exact format is reused:
```vest
const VERSION: u8 = 1

message = {
  const version: VERSION,
  payload: Tail,
}
```
Top-level constants are reusable constant parsers, not ordinary formats. A struct field that uses one must retain the `const` keyword, as in `const version: VERSION,`.

Integer literals may be decimal, hexadecimal such as `0xFF`, or a one-byte character such as `'A'` or `'\x00'`.

### Integer constraints
Append `|` to an integer format to constrain accepted values:
```vest
version = u8 | 1
payload_length = u16 | 8..0xFFFE
nonzero = u32 | 1..
small = u8 | ..31
```
Ranges are inclusive at both ends. A missing lower or upper bound means the range extends to the integer type's limit.

Use braces for a union of exact values and ranges:
```vest
message_type = u8 | { 1, 3, 8..15 }
```
Use `!` to accept the complement of a constraint:
```vest
not_reserved = u8 | !{ 0, 255 }
```
Constraints are local predicates on the parsed integer. Do not invent arbitrary boolean expressions such as `u16 | value > other_field`; that syntax is not supported.

## Byte arrays and repeated formats

### Fixed-length byte arrays
Use `[u8; N]` for exactly `N` bytes:
```vest
ipv6_address = [u8; 16]

message = {
  transaction_id: [u8; 12],
  source_address: ipv6_address,
}
```

### Constant byte arrays
An inline constant byte array validates exact bytes:
```vest
message = {
  const magic: [u8; 4] = [0x50, 0x4B, 0x03, 0x04],
  payload: Tail,
}
```
The declared array length must equal the number of values. A repeated literal may be written `[VALUE; COUNT]`:
```vest
const ZERO_PADDING: [u8; 8] = [0; 8]
```
An ASCII byte string can also initialize a constant byte array when its length matches:
```vest
const SIGNATURE: [u8; 4] = "TEST"
```

### Count-bounded repetition
The general array syntax is `[ELEMENT_FORMAT; COUNT]`. It parses `COUNT` consecutive elements, not `COUNT` bytes unless the element format is one byte wide.
```vest
word_list(@count: u16) = [u32; @count]

message = {
  @item_count: u16,
  items: [item; @item_count],
}
```

### Variable-length byte arrays
Pass a previously parsed dependent length to a helper or use it directly:
```vest
bytes(@length: u24) = [u8; @length]

message = {
  @payload_length: u24,
  payload: bytes(@payload_length),
}
```

Only a field marked with `@` can be referenced later. This is wrong:
```vest
bad = {
  length: u16,
  data: [u8; @length],
}
```
This is correct:
```vest
good = {
  @length: u16,
  data: [u8; @length],
}
```

## Structs and dependencies
Declare a struct with braces. Fields parse from top to bottom:
```vest
header = {
  version: u8,
  flags: u8,
  payload_length: u16,
}
```

An empty struct consumes zero bytes and is useful as an explicitly absent tagged alternative:
```vest
empty = {
}

optional_body(@present: u8) = choose(@present) {
  0 => empty,
  1 => body,
}
```
Use an empty format only when the selector explicitly identifies absence. Do not put an empty format inside `Vec` or `Option`, because a repeated or optional inner format must consume at least one byte when it succeeds.

Mark a field with `@` when its parsed value is needed later in the same struct:
```vest
record = {
  @length: u16,
  data: [u8; @length],
}
```

Dependent values may control array lengths, helper arguments, and choices. A use must come after the dependent field is parsed. Reference it as `@name`, never as `self.name` or a bare `name`.

Vest permits dependent access to a nested dependent field in length expressions:
```vest
header = {
  kind: u8,
  @payload_length: u16 | 4..,
}

message = {
  @header: header,
  payload: [u8; @header.payload_length - 4],
}
```
Every component along a referenced path must be available as a dependency. Use nested access only when it is clearer than flattening the controlling field into `message`. Prefer a simple dependency such as `@payload_length` for helper arguments and choice selectors.

### Parameterized helper formats
Use parameters when an enclosing format has already parsed a value needed by a helper:
```vest
payload(@length: u16) = {
  data: [u8; @length],
}

message = {
  @payload_length: u16,
  payload: payload(@payload_length),
}
```
Parameter declarations include a format, but invocations pass only dependent values such as `@payload_length`. Do not pass literals, arithmetic expressions, bare field names, or `self.field` as arguments.

Parameter types must exactly match the dependent value's format, including refinements. Prefer a named alias when a constrained type is shared:
```vest
presence = u8 | { 0, 1 }

optional_body(@present: presence) = choose(@present) {
  0 => empty,
  1 => body,
}

message = {
  @present: presence,
  body: optional_body(@present),
}
```
Do not declare the helper parameter as plain `u8` when the passed field has format `u8 | { 0, 1 }`; Vest treats those as different types.

The root `message` definition must never have parameters:
```vest
// Invalid entry point:
message(@length: u16) = [u8; @length]
```

## Length expressions
Array lengths may use integer constants, dependent values, statically known format sizes, parentheses, and `+`, `-`, `*`, or `/`:
```vest
fixed_header = {
  kind: u8,
  flags: u8,
  total_length: u16,
}

body(@total_length: u16 | 4..) = {
  data: [u8; @total_length - |fixed_header|],
}
```
`|format_name|` is the static byte size of a fixed-size format, and `|u16|` is `2`:
```vest
header_copy = [u8; |fixed_header|]
two_words = [u8; 2 * |u16|]
```
Use `|name|` only when the target has a statically known size. Do not use it for formats containing `Tail`, `Vec`, `Option`, dependent arrays, or variable-size choice branches.

Guard subtraction through the controlling integer's lower-bound constraint so a length cannot be smaller than the subtracted header size:
```vest
message = {
  @total_length: u16 | 4..,
  payload: [u8; @total_length - 4],
}
```
Avoid division by a value that can be zero and avoid length arithmetic that can overflow or underflow. Prefer a direct protocol byte length when one exists.

## Enumerations
Use an enum for a closed set of numeric protocol values:
```vest
message_kind = enum {
  Data = 1,
  Ack = 2,
  Error = 3,
}
```
Vest infers the enum's integer width. When the wire field must have a particular width, add matching type suffixes so small values are not incorrectly encoded as `u8`:
```vest
message_kind = enum {
  Data = 1u16,
  Ack = 2u16,
  Error = 3u16,
}
```
All typed values in one enum must use the same integer type. Enum labels must be unique.

Append `...` to model an open enum whose wire value may be unknown:
```vest
message_kind = enum {
  Data = 1,
  Ack = 2,
  ...
}
```

An enum-typed field may itself be constrained:
```vest
data_or_ack = message_kind | { Data, Ack }
not_error = message_kind | !Error
```

## Tagged choices
A dependent `choose` selects a format from a previously parsed tag. Prefer an enum tag for named protocol alternatives.

### Closed enum tag
Choices over a closed enum must include every enum variant and must not use `_`:
```vest
message_kind = enum {
  Data = 1,
  Ack = 2,
}

body(@kind: message_kind) = choose(@kind) {
  Data => data_body,
  Ack => ack_body,
}
```

### Open enum tag
Use `_` as the fallback for an open enum:
```vest
message_kind = enum {
  Data = 1,
  Ack = 2,
  ...
}

body(@kind: message_kind) = choose(@kind) {
  Data => data_body,
  Ack => ack_body,
  _ => Tail,
}
```

### Integer tag
Integer tags use exact values or inclusive ranges. Cases must not overlap:
```vest
body(@kind: u8) = choose(@kind) {
  0 => empty_body,
  1..15 => short_body,
  _ => Tail,
}
```
An omitted fallback means unmatched values are rejected. Use `_ => Tail` only when the specification explicitly permits unknown alternatives to contain arbitrary remaining bytes.

### Fixed byte-array tag
A dependent fixed byte array may also select a branch:
```vest
body(@tag: [u8; 2]) = choose(@tag) {
  [0x01, 0x00] => first_body,
  [0x02, 0x00] => second_body,
  _ => Tail,
}
```

## Length-delimited subformats
Use `>>=` when a byte length bounds a structured region. The format on the right must consume exactly the bytes captured on the left:
```vest
entry = {
  key: u8,
  value: u16,
}

entries(@byte_length: u16) = [u8; @byte_length] >>= Vec<entry>

message = {
  @entries_length: u16,
  entries: entries(@entries_length),
}
```
This differs from `[entry; @entries_length]`, which treats the value as an element count rather than a byte count.

Length-delimited tagged payloads are common:
```vest
payload(@kind: message_kind, @length: u16) = [u8; @length] >>= choose(@kind) {
  Data => data_body,
  Ack => ack_body,
}
```
Use `>>=` only after `[u8; LENGTH]` or `Tail`. Do not use it as a general-purpose conversion operator.

## Repetition
`Vec<FORMAT>` parses zero or more consecutive values until the end of its current input region:
```vest
entries(@byte_length: u16) = [u8; @byte_length] >>= Vec<entry>
```
The repeated format must always consume at least one byte. Never repeat `Option<...>`, an empty format, or another construct that may succeed without consuming input.

Prefer a count-bounded array when the protocol provides an element count:
```vest
entries(@count: u16) = [entry; @count]
```
Prefer a length-delimited `Vec` when the protocol provides a byte length. Do not use a root-level `Vec` unless the entire input is explicitly a sequence of records with no separate terminator or trailer.

## Optional values
Use `Option<FORMAT>` only for a format that is unambiguously recognizable from the next bytes:
```vest
extension = {
  const marker: u8 = 0xEE,
  value: u32,
}

optional_extension = Option<extension>
```
`Option<FORMAT>` first attempts `FORMAT`; if that parse fails, it succeeds without consuming bytes. The inner format must consume at least one byte. Do not use `Option` merely because a flag says a field is present. For flag-controlled presence, use a dependent `choose` with an explicit empty branch when an empty format is available, or structure the alternatives so their wire layouts are exact.

Do not use `Option<Tail>` or place an ambiguous optional field before data that could also begin with the optional format.

## Untagged choices
Use an untagged `choose` only when alternatives are distinguishable from their input encodings:
```vest
item = choose {
  Small(u8 | ..0xFE),
  Extended({
    const marker: u8 = 0xFF,
    value: u16,
  }),
}
```
Each branch has a unique variant label followed by its format in parentheses. Branches must be disjoint. Prefer a parsed tag plus `choose(@tag)` whenever the protocol has an explicit tag. Never use overlapping refinements or alternatives that are distinguishable only after one branch has already accepted the same prefix.

## Tail data
`Tail` consumes all bytes remaining in the current input region:
```vest
message = {
  kind: u8,
  payload: Tail,
}
```
Use `Tail` only for a payload explicitly defined as all remaining bytes, an unknown open-enum alternative, or opaque data whose enclosing region is already bounded. Because `Tail` consumes everything in its region, no field may follow it in the same sequential layout.

To reinterpret all bytes in a bounded region with another format, use `>>=`:
```vest
payload = {
  entries: Tail >>= Vec<entry>,
}
```
Keep `Tail` in a struct field or a choice branch; do not make a bare top-level definition such as `payload = Tail`.

## Constants, reserved fields, and padding
Use constant fields for mandatory magic values, fixed versions, reserved zero bytes, fixed discriminators, and required padding:
```vest
message = {
  const magic: [u8; 2] = [0xAB, 0xCD],
  const version: u8 = 1,
  const reserved: [u8; 3] = [0; 3],
  payload: Tail,
}
```
Use a normal named field instead when the specification transmits the value but does not require one exact encoding.

## Unsupported or structurally limited features
* The syntax in this document has no bitfield or sub-byte integer construct. Do not invent `u1`, `u4`, masks, shifts, or Rust expressions. If the specification packs several fields into one byte or word, parse the complete storage unit as `u8`, `u16`, `u32`, or `u64` with a descriptive name such as `packed_flags`; do not pretend the individual bits are separately parsed.
* The syntax in this document cannot mix big- and little-endian multi-byte integers in one file.
* Do not invent checksum, CRC, cryptographic verification, compression, decompression, string decoding, regular expressions, parser hooks, procedural code, or semantic callbacks. Represent such data structurally as bytes or integers.
* Integer refinements validate only constants, ranges, sets, and their complements. They cannot express arbitrary relationships between two parsed scalar values.
* Do not create recursive format definitions. Use bounded arrays or length-delimited `Vec` for repeated wire structures.

## Common protocol patterns

### Fixed header and variable payload
```vest
!BIG_ENDIAN

message = {
  version: u8,
  message_type: u8,
  @payload_length: u16,
  payload: [u8; @payload_length],
}
```

### Total length including a fixed header
```vest
!BIG_ENDIAN

message = {
  const version: u8 = 1,
  flags: u8,
  @total_length: u16 | 4..,
  payload: [u8; @total_length - 4],
}
```

### Tag-length-value
```vest
!BIG_ENDIAN

value_kind = enum {
  Byte = 1,
  Word = 2,
  Raw = 3,
}

value(@kind: value_kind, @length: u16) = [u8; @length] >>= choose(@kind) {
  Byte => u8,
  Word => u16,
  Raw => Tail,
}

message = {
  @kind: value_kind,
  @length: u16,
  value: value(@kind, @length),
}
```

The `Byte` and `Word` branches reject a `length` other than 1 or 2 because the right-hand format must consume the entire bounded region.

### Counted records
```vest
!BIG_ENDIAN

record = {
  identifier: u16,
  value: u32,
}

message = {
  @record_count: u16,
  records: [record; @record_count],
}
```

### Byte-length-bounded records
```vest
!BIG_ENDIAN

record = {
  @record_length: u16,
  data: [u8; @record_length],
}

message = {
  @records_length: u16,
  records: [u8; @records_length] >>= Vec<record>,
}
```

## Final requirements
Before producing the grammar, ensure all of the following are true:

1. The response contains only Vest source text for one `.vest` file.
2. There is exactly one unparameterized root struct named `message`.
3. `message` consumes the complete protocol data unit.
4. The file uses only syntax documented here.
5. The file-wide byte order matches the specification.
6. Every referenced dependency is marked with `@` and parsed or passed before use.
7. Every struct field, enum variant, and choice branch has a trailing comma.
8. Length fields control the correct unit: bytes for `[u8; length]` and `>>=`, elements for `[format; count]`.
9. Tagged choices are exhaustive where required, and untagged choices are disjoint.
10. Constants and constraints enforce protocol validity without inventing facts absent from the specification.
