# spicy incorrectly parses a bitfield with field value constraints

The following grammar contains a constraint that the x field inside the bitfield should be zero, which causes Spicy to incorrectly parse an input ignoring the MSB0 bit order.

```spicy
module Bitfield;

import spicy;

public type Main = unit {
    bf: bitfield(16) {
        x: 0..7 = 0;
        y: 8..15;
    } &bit-order=spicy::BitOrder::MSB0;
    on %done { print self; }
};
```

```bash
printf "\xff\x00" | spicy-driver ./playground/bitfield-incorrect-parsing.spicy
[$bf=(x: 0, y: 255)]
```

As a baseline, if we remove the `= 0` constraint inside the bitfield, the MSB0 bit order is respected and the output changes:

```spicy
module Bitfield;

import spicy;

public type Main = unit {
    bf: bitfield(16) {
        x: 0..7;
        y: 8..15;
    } &bit-order=spicy::BitOrder::MSB0;
    on %done { print self; }
};
```

```bash
printf "\xff\x00" | spicy-driver ./playground/bitfield-correct-parsing.spicy
[$bf=(x: 255, y: 0)]
```

The first grammar appears to be ommiting the `&bit-order` attribute and using the default order spicy uses.
