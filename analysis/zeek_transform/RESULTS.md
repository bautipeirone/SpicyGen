# Throw/require comparison on GPT-5.4 grammars

Run on 2026-09-27 with `compare.py`. Grammar paths in the table are relative
to `~/imdea/spec-extract/`. Packets came from `data/packets/<protocol>`;
each `.bin` payload was
wrapped in a separate synthetic flow. Reports, per-packet results, captures,
and Zeek logs are under `tmp/zeek-transform-results/<protocol>/`.

| Protocol | Grammar run | Transport | Payloads | Parsed original → transformed | Violations original → transformed | Weirds original → transformed |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Babel | `data/workspaces/babel/gpt-5.4/20260724_120708/grammar/output.spicy` | UDP | 21,639 | 21,639 → 21,639 | 0 → 0 | 0 → 0 |
| BFD | `data/workspaces/bfd/gpt-5.4/20260724_105642/grammar/output.spicy` | UDP | 5,737 | 5,737 → 5,737 | 0 → 0 | 0 → 0 |
| BGP | `data/workspaces/bgp/gpt-5.4/20260724_105646/grammar/output.spicy` | TCP | 96 | 80 → 80 | 16 → 16 | 0 → 10 |
| RTSP | `data/outputs/20260629_114229/rtsp-gpt-5.4.spicy` | TCP | 220,080 | 220,080 → 220,080 | 0 → 0 | 0 → 0 |
| RTP | `data/workspaces/rtp/gpt-5.4/20260729_104101/grammar/output.spicy` | UDP | 215,043 | 215,043 → 215,043 | 0 → 0 | 0 → 0 |

The RTSP timestamp does not exist under `data/workspaces/rtsp`; its grammar is
under `data/outputs/20260629_114229`.

In BGP, five `bgp_infinite_loop` payloads each generated two weirds after the
rewrite: `invalid UPDATE payload length` and `invalid UPDATE message length`.
The original grammar threw on the UPDATE length. The transformed grammar
continued parsing and then failed while reading 255 bytes with only six
available. Thus the rewrite changed the failure reason but did not reduce the
number of analyzer violations on these payloads.

These are raw analyzer violation event counts, not counts of distinct malformed
packets. "Parsed" means the entry unit completed in this isolated replay; it
does not establish protocol validity or reproduce original capture sessions.

## Why most corpus results did not change

The five input grammars contain **43 literal `throw` statements and no
`&requires` attributes**. All 43 throws were replaced in the transformed
sources. The zero weird counts therefore do not come from a missed `&requires`
rewrite. To check the Zeek measurement path, the same comparison was run on
the repository's `data/raw/<protocol>/negative` fixtures:

| Protocol | Negative fixtures | Original violations | Transformed violations | Transformed weirds |
| --- | ---: | ---: | ---: | ---: |
| Babel | 10 | 10 | 2 | 8 |
| BFD | 10 | 10 | 0 | 10 |
| BGP | 10 | 10 | 10 | 15 |
| RTSP | 5 | 5 | 3 | 3 |
| RTP | 10 | 10 | 6 | 4 |

Those control runs logged the expected throw-derived weirds for every grammar.
Their files are in `tmp/zeek-transform-diagnostic/<protocol>/`.

- **Babel:** The guards reject zero AckReq/IHU intervals, all-zero/all-one
  router IDs, invalid address encodings, omitted prefixes without previous
  state, finite wildcard metrics, and invalid route/sequence requests. The
  21,639 corpus payloads contained 422,307 Update TLVs, including 189,394
  with omitted prefix bytes. None lacked the required prior prefix state.
  The other inspected malformed field combinations were also absent. Negative
  fixtures triggered eight of these guards, confirming they can fire.
- **BFD:** The guards check version, flags, detection multiplier, packet and
  authentication lengths, discriminators, and timer values. All 5,737 corpus
  payloads had version 1; their relevant flag, length, discriminator, and timer
  checks passed. The 71 authenticated payloads used matching lengths:
  MD5 `24/24` (31), SHA1 `28/28` (25), and simple password `9/9` (15).
  The corpus has only 157 distinct payload byte strings, so the large packet
  count does not imply broad malformed-input coverage.
- **BGP:** Five corpus payloads from `bgp_infinite_loop` reached the invalid
  UPDATE length throws. After replacement they emitted two weirds each, then
  failed while trying to read 255 bytes with only six available. The other 11
  violation events were parser errors unrelated to a `throw`: nine short
  integer reads, one short byte read, and one integer overflow.
- **RTSP:** The four throws concern missing `CSeq`, a body without
  `Content-Length`, and unexpected trailing bytes. Of 220,080 payloads,
  216,076 were interleaved `$` frames and bypassed the text-message branch.
  All 4,004 remaining messages contained `CSeq`; all 400 with a body had a
  `Content-Length`, and the observed body sizes matched it. Negative fixtures
  triggered missing `CSeq` and unexpected-body guards.
- **RTP:** The guards reject versions other than 2, payload types 72/73,
  and invalid padding. All 215,043 corpus payloads used version 2, no padding,
  and payload type 96 or 34. The grammar also defines an RTCP-version throw,
  but its public `Message` unit contains only `rtp: RTPPacket`; the RTCP parser
  is unreachable from this entry point. Negative fixtures triggered the
  version and reserved-type guards. Two padding fixtures instead failed in
  `self.payload.sub(...).to_uint()` before reaching their explicit throws.

Corpus diversity is also limited by source captures: 21,629 of 21,639 Babel
payloads, 5,621 of 5,737 BFD payloads, 220,074 of 220,080 RTSP payloads,
and 214,998 of 215,043 RTP payloads came from one capture per protocol.
These observations support the hypothesis that the current extracted traffic
rarely enters the malformed-input guards. They do not establish that replacing
throws will suppress all violations on malformed or adversarial traffic.
