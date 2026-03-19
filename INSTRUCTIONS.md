# Grammar specification language

## General guidelines
> Generally, you define parsers through types called “units” that describe the syntax of a protocol. A set of units forms a grammar. In practice, units typically correspond pretty directly to protocol data units (PDUs) as protocol specifications tend to define them.
> One will typically create a unit type for each of the main data units that a protocol defines.
> Subunits don't have to repeat fields declared in the parent units.

## Grammar
* Every script starts with a `module` statement defining a namespace for the script’s content.
* The layout of a piece of data is defined by creating a `unit` type. The type lists individual *fields* in the order they are to be parsed.
* Each field inside a unit has a type and an optional name. The type defines how that field will be parsed from raw input data. If a field has a name, it can later be referenced to access its value. Consequently, all fields with semantic meanings have names, while those which are unlikely to be relevant later do not (e.g., whitespace).
* A unit field can have another unit as its type. If the subunit receives parameters, they must be given right after the type.
* Fields of integer type can be either signed (`intN`) or unsigned (`uintN`). In either case, the bit length N determines the number of bytes being parsed.
* The `public` keyword exposes the generated parser of a unit to to external host applications wanting to deploy it.
* Unit types can receive parameters upon instantiation, which will then be available to any code inside the type’s declaration.
* When parsing a field of type Bytes, raw input bytes will be consumed according to a specified attribute that determines when to stop. The following attributes are supported, and at least one of these attributes must be provided:
  * &eod: Consumes all subsequent data until the end of the input is reached.
  * &size=N: Consumes exactly N bytes. The attribute may be combined with &eod to consume up to N bytes instead (i.e., permit running out of input before the size limit is reached).
  * &until=DELIM: Consumes bytes until the specified delimiter is found. DELIM must be of type bytes itself. The delimiter will not be included into the resulting value, but consumed.
  * &until-including=DELIM: Similar to &until, but this does include the delimiter DELIM into the resulting value.
* Parsing a vector creates a loop that repeatedly parses elements of the specified type from the input stream until an end condition is reached.
The syntax for a known size vector is `NAME : ELEM_TYPE[SIZE]`. It is possible to skip the SIZE (e.g., x: uint8[]) and instead use another kind of end conditions to terminate a vector’s parsing loop:
  * &eod: Parses elements until the end of the input stream is reached.
  * &size=N: Parses the vector from the subsequent N bytes of input data.
* Comment lines only start with the `#` symbol. Multiline comments are not supported.
* You can use `void` as type for empty fields (i.e. 0 byte fields).

# Notes
* For multi-byte integer values, it is important to consider the byte order for parsing.
  Network byte order (i.e., big-endian) is the default. To change byte order, use field attributes.
* Anonymous fields are declared specifying no name to the field. This can be used for special packet structures that do not correspond to packet fields (i.e. mandatory bytes at start of field, padding) (skip fields can be used otherwise).
* All sentences like `type` declarations, `module`, etc. end with a `;`.
* You cant use the following as field identifiers since they are reserved keywords:
  * file
  * port


## Examples
* Switch constructs are supported as a way to branch into one of several parsing alternatives.
```
public type Foo = unit {
    x: bytes &size=1;
    switch ( self.x ) {
        b"A" -> a8: int8;
        b"B" -> a16: int16;
        b"C" -> a32: int32;
        * -> : void; # Dont parse anything
    };
};
```
* In the following example, bar is a **constant field** that only matches "bar",
whereas foo matches the assigned regular expression. Fields that match a **regex**
get assigned the type Bytes and the value of what matches the regex.
```
public type Foo = unit {
    bar: b"bar";
    foo: /Foo.*/;
};
```

* Fields can have **constraints** on their values. The following example requires x to
be less than 5 ($$ is used to access the parsed value).
```
type Foo = unit {
    x: int8 &requires=($$ < 5);
};
```
* The **size** attribute specifies the size in bytes a field must consume. The value for the size attribute can reference
unit parameters or previous field's values.
```
public type Foo = unit {
    l: int8;
    x: int8 &size=self.l;
    y: bytes &eod;
};
```
* **Bitfields** parse an integer value of a given size, and then make selected smaller bit ranges within that value available individually
through dedicated identifiers. For example, the following unit parses 4 bytes as an uint32 and then makes the value of bit 0 available as
f.x1, bits 1 to 2 as f.x2, and bits 3 to 4 as f.x3, respectively:
```
public type Foo = unit {
    f: bitfield(32) {
        x1: 0;
        x2: 1..2;
        x3: 3..4;
    };
};
```

