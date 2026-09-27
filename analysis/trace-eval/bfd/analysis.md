# BFD trace comparison

This analysis uses the existing [summary.json](summary.json) and [comparison.csv](comparison.csv); the parsers were not rerun. The extracted packet bytes, capture frame tails, saved oracle verdicts, and parser source were checked to explain every disagreement.

All **5,737** extracted BFD packets match oracle frames, and the oracle accepts all 5,737. `BFD-GPT` accepts all 5,737; `BFD-GPT-No-Spec` accepts 5,666 and rejects **71**. The parsers disagree on exactly those 71 packets. There are no oracle-only or packet-only coverage gaps.

## All 71 disagreements have the same cause

| Capture; disagreeing frames | Count | BFD length / extracted bytes | Authentication |
| --- | ---: | --- | --- |
| `bfd-raw-auth-simple.pcap`; 1–15 | 15 | 33 / 37 bytes | Simple Password, auth type 1, auth length 9 |
| `bfd-raw-auth-md5.pcap`; 1–31 | 31 | 48 / 52 bytes | Keyed MD5, auth type 2, auth length 24 |
| `bfd-raw-auth-sha1.pcap`; 1–25 | 25 | 52 / 56 bytes | Meticulous Keyed SHA1, auth type 5, auth length 28 |

In every disagreeing packet, the BFD header length equals the UDP payload length reported by TShark. The extractor's `.bin` file is **four bytes longer**. Its final four bytes match the captured Ethernet FCS in **all 71 frames**. For example, the first MD5 frame declares BFD length 48 and has UDP payload length 48, while the extracted file is 52 bytes and ends with FCS `3cc3f821`. TShark separates that FCS from the IP/UDP/BFD packet and accepts the BFD message.

The [No-Spec grammar](../../../../../data/eval/20260731_104101_No-Spec-Experiment/runs/bfd__gpt-5.4__no-spec__spicy/output.spicy) computes how many bytes remain within the BFD length, then rejects any `extra_data` after it with `extra bytes beyond declared packet length` (lines 109–110 and 152–154). The [GPT grammar](../../../../../data/workspaces/bfd/gpt-5.4/20260724_105642/grammar/output.spicy) parses the declared authentication section but has no end-of-packet check after it, so it accepts the appended FCS. The difference is **which bytes the two grammars permit beyond the BFD message**, triggered here by extractor inclusion of link-layer FCS. It does not show that the 71 BFD messages themselves are malformed.

All other captures (`bfd-real.pcap`, `bfd-multihop.pcap`, and `bfd-lag.pcap`) contain 5,666 extracted packets whose byte count equals the declared BFD length; both parsers accept all of them. This trace has no oracle-rejected BFD packets, so it cannot measure either parser's ability to reject malformed BFD messages.

For a comparable rerun, extraction should stop at the UDP payload length so the Ethernet FCS is not passed to either BFD parser.
