# Zeek module replay: representative captures

These are single-capture checks of the seven generated modules in
`../zeek-integration/`, run on 2026-09-27 with Zeek 9.0.0 and the locally
compiled Spicy integrations. They establish that the collector records the
requested metrics; they are not a complete traffic corpus or a throughput
benchmark. Each module was loaded alone, with its full capture replayed by
`zeek -r`.

| Protocol | Capture | Parsed messages | Violation events | Logged weird rows | Replay settings |
| --- | --- | ---: | ---: | ---: | --- |
| Babel | `babel/babel-diverse.pcap` | 21,629 | 0 | 0 | `-C` |
| BFD | `bfd/bfd-real.pcap` | 5,621 | 0 | 0 | `-C`, port 4784 |
| DHCP | `dhcp/dhcpfo.pcapng` | 15 | 0 | 0 | Default |
| DNS | `mixed/log1.pcap` | 664 | 0 | 159 | Default |
| HTTP | `http1.1/c05-http-reply-r1.pcap` | 6,904 | 0 | 31 | Default |
| RTSP | `rtsp/local-network.pcap` | 399 | 0 | 0 | `-C`, port 8554 |
| RTP | `rtp/h263-over-rtp.pcap` | 45 | 0 | 0 | `-C`, port 32976 |

All captures are under `../../data/pcap/` relative to the `grammar-generator`
directory.
`-C` was needed where Zeek warned about invalid or offloaded checksums;
without it, Babel yielded only 800 messages and BFD and RTSP yielded zero.
The BFD, RTSP, and RTP port overrides activate the analyzer on ports found
in their respective traces. RTP is the sum of RTP and RTCP packet events.

DNS weirds comprise 67 `invalid rr type`, 65 `invalid rr class`, 23
`invalid qtype`, and 4 `reserved Z bits must be zero`. All 31 HTTP weirds
are `both Transfer-Encoding and Content-Length present`. The weird metric
counts rows in `weird.log` whose `source` is the selected analyzer. It does
not count distinct pcap frames.

Parsed messages come from the module's top-level event, not protocol log
rows. On a small RTSP verification trace, the module parsed four messages
but wrote two `rtsp.log` rows because it logs at session finalization.
Violations count raw `analyzer_violation_info` events. A deliberately
truncated BFD datagram produced two such events: one explicit module
rejection and one Spicy parse error. Thus violation events must not be
interpreted as distinct malformed packets.

The DHCP, DNS, and HTTP packages also contain `uint16` debug counters that
print for every accepted or rejected message. The collector does not read
these counters. Their output can grow large on long traces, and their
16-bit range should be reviewed before runs exceeding 65,535 messages.
