# DNS trace comparison

This analysis uses the existing [summary.json](summary.json) and [comparison.csv](comparison.csv); the parsers were not rerun. Parser errors were grouped, and representative packet fields were checked against the parser constraints.

All **4,360** extracted DNS messages have an oracle frame. The oracle accepts 4,359 and soft-rejects one. `GPT-5.4-DNS` accepts all 4,360. The other target results are:

| Parser | Accept | Reject | Main limitation |
| --- | ---: | ---: | --- |
| GPT-5.4-NoSpec-DNS | 2,524 | 1,836 | Treats DNS class fields as a small enum. |
| GPT-5.4-WNP-DNS | 1,816 | 2,544 | Restricts DNS types/classes and conflates AD/CD with reserved Z bits. |
| GPT-5.4-3D-DNS | 4,202 | 158 | Fixed-depth name parsing, a four-question case limit, and the same AD/CD bit issue. |

The target decisions differ on **2,596 messages**. The reject counts overlap; they must not be added to get a count of distinct packets. Counts below classify each parser's first reported error.

## No-Spec Spicy: 1,836 rejects

| First error | Count | Actual wire value and cause |
| --- | ---: | --- |
| Invalid question class | 265 | `QCLASS=0x8001`: the high mDNS unicast-response bit is set over class IN. The parser converts the whole 16-bit value to an enum containing only classes 1–4, 254, and 255. |
| Invalid resource-record class | 579 | `CLASS=0x8001`: the mDNS cache-flush bit is set over class IN. The same enum conversion rejects it. |
| Invalid resource-record class | 992 | An OPT pseudo-record uses the CLASS field for advertised UDP payload size, not a DNS class. The observed first offending values are 512 (460 packets), 4000 (284), 1472 (194), 1440 (36), and 1232 (18). The enum conversion rejects them. |

For example, `log-bravo.pcap` frame 37 begins with mDNS answer class `0x8001`; frame 40 has question class `0x8001`. The reference parser and oracle accept both.

## WNP Spicy: 2,544 rejects

| First error | Count | Actual wire value and cause |
| --- | ---: | --- |
| Invalid `qtype` | 698 | Query type 65 (HTTPS, 610 packets) or 33 (SRV, 88) is absent from its fixed allowlist. Example: `dns.cap` frame 28 queries SRV. |
| Invalid `rr type` | 999 | The first unsupported RR type is 41 (OPT, 581 packets), 33 (SRV, 414), or 46 (RRSIG, 4). The grammar's RR allowlist stops at older types such as AAAA and LOC. |
| Invalid `qclass` | 263 | Question class `0x8001`, with the mDNS high bit set, is compared as an ordinary class number. |
| Invalid `rr class` | 576 | RR class `0x8001`, with the mDNS cache-flush bit set, is compared as an ordinary class number. |
| Reserved Z bits must be zero | 8 | These DNSSEC responses set AD (`0x20` in the second flags byte), while the actual reserved Z bit is zero. The grammar groups Z, AD, and CD into one field and requires all three bits zero. Example: `log1.pcap` frame 29757. |

The type and class categories are the **first** failing check. A packet can contain additional modern RR types or OPT values later in the same message.

## 3D: 158 rejects

| Error | Count | Cause |
| --- | ---: | --- |
| `_LabelOrRoot0` impossible | 148 | The generated name grammar has a fixed nesting depth (`Name9` down to `Name0`). These packets contain 33- or 34-label, reverse-address-style names, beyond that depth. All occur in `log-bravo.pcap`; frame 1723 is an example. |
| Message bitfield constraint | 8 | The same eight DNSSEC AD-bit packets rejected by WNP. The 3D grammar also constrains the three Z/AD/CD positions to zero. |
| Resource-record RDATA “not enough data” | 2 | `log-bravo.pcap` frames 6854 and 6855 have six questions. The 3D `QuestionList` handles only counts 0–4; its default case consumes no questions, misaligning subsequent RR parsing and producing the downstream RDATA error. The wire RDATA itself is not shown malformed by this error. |

## Oracle soft rejection

`log-stansted.pcap` frame **2547** is accepted by all DNS parsers but soft-rejected by the oracle for `Illegal checksum value (0)` in the enclosing UDP packet. The DNS message is only the extracted payload, so this is a transport-layer difference rather than a DNS parser disagreement.

The standard GPT-5.4 DNS parser agrees with the oracle on all 4,359 oracle-accepted messages, while its acceptance of the one soft reject is explained by the UDP checksum. This dataset does not establish how it behaves on malformed DNS payloads: the oracle has no DNS strict rejects here.
