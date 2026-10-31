# DNS trace comparison

Updated on **2026-10-30** with a fresh evaluation of the supplied [Zeek DNS grammar](../../../examples/spicy/dns.spicy), recorded as `Zeek-DNS`. It accepts all **4,360** extracted messages, with no parser rejections or execution errors, and matches `GPT-5.4-DNS` on every packet.

The results were merged into [summary.json](summary.json) and [comparison.csv](comparison.csv), using the existing four target runs and Wireshark oracle reports. All manifest payload hashes were verified, and every existing comparison field, target total, pairwise count, and per-capture count was reproduced before adding Zeek. Parser errors below retain the earlier grouping and packet analysis.

All **4,360** extracted DNS messages across ten captures have an oracle frame. The oracle accepts 4,359 and soft-rejects one; there are no strict rejects or coverage gaps.

| Parser | Accept | Reject | Main limitation |
| --- | ---: | ---: | --- |
| Zeek-DNS | 4,360 | 0 | No DNS rejection in this dataset; unsupported RDATA types can be consumed as bytes. |
| GPT-5.4-DNS | 4,360 | 0 | No DNS rejection in this dataset. |
| GPT-5.4-NoSpec-DNS | 2,524 | 1,836 | Treats DNS class fields as a small enum. |
| GPT-5.4-WNP-DNS | 1,816 | 2,544 | Restricts DNS types/classes and conflates AD/CD with reserved Z bits. |
| GPT-5.4-3D-DNS | 4,202 | 158 | Fixed-depth name parsing, a four-question case limit, and the same AD/CD bit issue. |

The target decisions differ on **2,596 messages**, unchanged after adding Zeek because its decisions match the standard GPT parser. The reject counts overlap; they must not be added to get a count of distinct packets. Counts below classify each parser's first reported error.

## Zeek Spicy: agreement and disagreement causes

Zeek accepts all **4,359 oracle-accepted** messages. Its only difference from the oracle is acceptance of `log-stansted.pcap` frame **2547**, whose soft rejection comes from the enclosing UDP checksum, as detailed below. There are no new packets that require a DNS failure explanation.

| Other parser | Both accept | Zeek accepts / other rejects | Both reject |
| --- | ---: | ---: | ---: |
| GPT-5.4-DNS | 4,360 | 0 | 0 |
| GPT-5.4-NoSpec-DNS | 2,524 | 1,836 | 0 |
| GPT-5.4-WNP-DNS | 1,816 | 2,544 | 0 |
| GPT-5.4-3D-DNS | 4,202 | 158 | 0 |

The supplied grammar explains these differences:

- **mDNS and OPT classes:** `Question.qclass` and `ResourceRecord.class` are unconstrained `uint16` fields. Zeek accepts the mDNS `0x8001` values and OPT advertised UDP sizes rejected by No-Spec and WNP. It does not apply either grammar's class allowlist.
- **DNS record types:** `Question.qtype` is also an unconstrained `uint16`, so HTTPS and SRV questions pass. Resource records have an explicit SRV decoder; OPT and RRSIG data go through the default `bytes &eod` branch bounded by `rdlen`. The `RDType` conversion does not reject the observed unlisted RRSIG type. Acceptance of these records establishes that their framing parses, without establishing semantic validation of the opaque RDATA.
- **AD flags:** the header exposes AD and CD and does not assert that the overlapping `z` field is zero. All eight AD-bit responses rejected by WNP and 3D pass Zeek, including `log1.pcap` frame 29757.
- **Long names and question counts:** `Name` reads a variable number of labels and follows compression pointers; its accumulated-name guard is 1,024 bytes. `Message.question` uses the actual `qdcount`. Consequently, Zeek accepts all 148 name-depth failures and both six-question packets (`log-bravo.pcap` frames 6854 and 6855) rejected by 3D.

The remaining sections account for every rejection represented in this table. Zeek accepts every packet in each listed category.

The grammar's `Header.rejected` variable describes a DNS response condition. Setting that variable does not throw a parse error; the comparison records whether `spicy-dump` successfully parses the message.

The fresh run compiled the unmodified `DNS::Message` grammar with Spicy **1.17.0** and used 12 workers. The sandbox blocked the standard runner's multiprocessing setup after compilation, so the evaluation used threads to invoke the same runtime and one `spicy-dump` process per payload. The merged summary records the grammar SHA-256, runtime version, run timestamp, and reused-result hashes under `merge_provenance`. Full new packet results are in [Zeek-DNS.jsonl](targets/Zeek-DNS.jsonl), with its [run summary](targets/Zeek-DNS.summary.json). This evaluates the supplied standalone grammar; it does not replay captures through the complete Zeek analyzer.

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

Both Zeek and the standard GPT-5.4 DNS parser agree with the oracle on all 4,359 oracle-accepted messages, while their acceptance of the one soft reject is explained by the UDP checksum. This dataset does not establish how either behaves on malformed DNS payloads: the oracle has no DNS strict rejects here.
