# WNP Spicy parser with throws rewritten to prints

Run on **2026-11-12** with Spicy 1.17.0 and 12 workers. This repeats the
[DNS trace comparison](../analysis.md) for the `GPT-5.4-WNP-DNS` grammar after
applying the grammar transformation that `create_zeek_package.py` uses when it
builds a Zeek package. The builder's `adapt_parser_to_zeek_analyzer` rewrites
every `throw "..."` statement and `&requires=(...)` attribute into a
`zeek::weird` call so that the analyzer logs the condition and keeps parsing.
[transform_to_prints.py](../../transform_to_prints.py) applies that same
function and then swaps the sink: each `zeek::weird("m")` becomes `print "m"`
and the `import zeek;` line is dropped, so the result runs under standalone
`spicy-dump`. The grammar contains **8 throws and no `&requires`**; all eight
were rewritten, and nothing else changed
([inputs/original.spicy](inputs/original.spicy) vs
[inputs/transformed.spicy](inputs/transformed.spicy), hashes in
[provenance.json](provenance.json)).

The original grammar was run again as a control. It reproduced the earlier
per-packet verdicts exactly: **1,816 accepted, 2,544 rejected**, no differences
on any packet. Oracle reports were reused from [../oracle](../oracle).

| Parser | Accept | Reject | Packets with at least one print |
| --- | ---: | ---: | ---: |
| GPT-5.4-WNP-DNS (control) | 1,816 | 2,544 | n/a |
| GPT-5.4-WNP-Print-DNS | 4,360 | 0 | 2,544 |

The transformed grammar accepts all 4,360 messages, matching `Zeek-DNS` and
`GPT-5.4-DNS`, including the one oracle soft reject (a UDP checksum issue
outside the DNS payload). Every message the control rejected now parses to
completion. The set of packets that emit at least one print is **exactly** the
control's 2,544 rejected packets, and for each of them the first print equals
the control's first error, so the rewrite changed no guard evaluation; it only
stopped the guards from aborting the parse.

## What the guards reported once parsing continued

`spicy-dump` hides prints by default. [collect_prints.py](collect_prints.py)
reran the compiled parser with `-P` over the [packet list](packet-list.txt)
and recorded every print per packet in [prints.jsonl](prints.jsonl).

| Message | As first print | Total prints |
| --- | ---: | ---: |
| `invalid rr type` | 999 | 4,481 |
| `invalid qtype` | 698 | 698 |
| `invalid rr class` | 576 | 5,173 |
| `invalid qclass` | 263 | 517 |
| `reserved Z bits must be zero` | 8 | 8 |
| `invalid domain name component` | 0 | 0 |
| `invalid compression pointer offset` | 0 | 0 |
| **Total** | **2,544** | **10,877** |

The first-print column reproduces the first-error table in the main analysis.
The total column is new information: the original parser stopped at the first
failing check, while the transformed one reports every violation in the
message. The 2,544 flagged messages carried **10,877** guard violations, and
**1,757** of them triggered more than one distinct message. Per-packet print
counts range from 1 to 16; the 330 packets with 14 prints are mDNS responses
whose many records each fail both the type and class allowlists.

Per capture, the flagged packets are: `log-bravo.pcap` 1,680, `log2.pcap` 380,
`log1.pcap` 328, `log3.pcap` 120, `log-stansted.pcap` 18, `log4.pcap` 12, and
`dns.cap` 6. The remaining three captures produce no prints.

## Why continuation works for this grammar

The rewrite is safe here because every guard sits after a fixed-width field
and leaves the cursor in a well-defined place. Unknown `rr_type` values fall
into the `* -> rdata: bytes &size=self.rdlength` switch branch, so OPT, SRV and
RRSIG records are consumed as opaque bytes bounded by `rdlength`. Class and
type checks on questions do not affect framing. The Z-bit check only inspects
the flags. The two domain-name guards never fired in this dataset. In a grammar
whose throw protects a length computation or a branch that would otherwise
read misaligned data, continuing after the print could still fail later with a
different error, as seen for BGP in the
[earlier transformation study](../../../zeek_transform/trace-comparison/RESULTS.md).

## What this measures

Acceptance means a successful `spicy-dump` exit on the extracted payload. A
print is a logging side effect; it is not a Zeek `weird.log` row, and this run
does not exercise Zeek flow handling. Parsed field values were not compared
against the other parsers.

## Files

- [summary.json](summary.json), [comparison.csv](comparison.csv) and
  [packet-list.txt](packet-list.txt) from `compare_traces.py`.
- [targets/](targets/): per-packet JSONL and run summaries for both grammars.
- [prints.jsonl](prints.jsonl): exit code and ordered print lines per packet.
- [provenance.json](provenance.json): grammar hashes, commands and validation.
