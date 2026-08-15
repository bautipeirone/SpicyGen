## Description

Using a Spicy DNS analyzer with `replaces DNS` in the EVT definition causes Zeek to segfault when processing a PCAP containing DNS traffic over TCP.

This does not appear to be specific to a custom DNS implementation. I can reproduce the same behavior using the Spicy DNS analyzer provided by the Zeek project.

The crash disappears completely when the `replaces DNS` directive is removed from the EVT file.

This may be related to a previous issue I found, #3787, but I am reporting it separately because replacing the built-in DNS analyzer with a UDP Spicy DNS analyzer appears to be a supported configuration, and this issue can be reproduced with the Zeek-provided analyzer itself.

## Reproduction

The relevant analyzer definition is equivalent to:

```spicy
protocol analyzer spicy::DNS over UDP:
    parse originator with DNS::Message,
    parse responder with DNS::Message,
    replaces DNS;
```

After compiling the analyzer, running:

```bash
zeek -Cr trace.pcap build/message.hlto
```

results in a segmentation fault.

The PCAP contains UDP DNS traffic as well as **TCP traffic to port 53**. I can attach the PCAP to this issue since it reliably reproduces the crash.

Zeek segfaults while processing the PCAP when the Spicy analyzer contains:

```spicy
replaces DNS;
```

Removing only that directive:

```spicy
protocol analyzer spicy::DNS over UDP:
    parse originator with DNS::Message,
    parse responder with DNS::Message;
```

allows the same analyzer and the same PCAP to run without the segmentation fault.

In other words:

```text
Spicy DNS analyzer + PCAP + `replaces DNS`    -> segmentation fault
Spicy DNS analyzer + same PCAP + no replaces -> works
```

## Additional observations

The PCAP contains DNS traffic over TCP on port 53. My suspicion is that this is what triggers the problem.

The built-in Zeek DNS analyzer supports both TCP and UDP, while the replacement Spicy analyzer is declared:

```spicy
over UDP
```

It therefore looks possible that after `replaces DNS` is registered, traffic or analyzer state associated with TCP DNS is being redirected to the UDP Spicy analyzer.

This seems similar to the transport-layer mismatch described in #3787 ("Spicy: Using `replaces` with a mismatching transport-layer analyzer can lead to crashes").

## Environment

```text
Zeek version: 8.2.1
Spicy version: 8.2.1
OS: Ubuntu 24.04.4 LTS
```

I can also provide a backtrace or run the reproducer under ASAN/GDB if that would help identify where the invalid analyzer dispatch occurs.