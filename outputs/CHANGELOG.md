# Stable Changelog

This changelog tracks which release grammars were updated in `stable` and `wip`. Grammars in the latter one don't compile yet or need polishing via prompt refinement.

## Summary

| Protocol | qwen3-coder:480b-cloud Compiles | qwen3-coder:480b-cloud Packet test | gemini-3.1-flash-lite-preview Compiles | gemini-3.1-flash-lite-preview Packet test |
| --- | --- | --- | --- | --- |
| IRC | Yes | - | Yes | - |
| DHCP | Yes | - | Yes | - |
| ICMPv4 | Yes | - | Yes | - |
| VXLAN | Yes | - | Yes | - |
| UDP | Yes | - | Yes | - |
| BFD | Yes | - | Yes | - |
| DNS | Yes | - | Yes | - |
| SPP | Yes | - | Yes | - |
| TFTP | Yes | - | Yes | - |
| RSTP | Yes | - | Yes | - |
| LDAP | No | - | No | - |
| IPv4 | Yes | - | Yes | - |
| IPv6 | Yes | - | No | - |
| TCP | Yes | - | Yes | - |

## 2026-04-08

Add release grammar: `IRC`, `IPv4`, `IPv6`, `TCP` \
Change release grammar: `DHCP`

### IPv4

Promoted version: `20260408_161051`

| Version | Compiles | Packet parsing test |
| --- | --- | --- |
| `qwen3-coder:480b-cloud` | Yes | - |
| `gemini-3.1-flash-lite-preview` | Yes | - |

### IPv6

Promoted version: `20260408_161051`

| Version | Compiles | Packet parsing test |
| --- | --- | --- |
| `qwen3-coder:480b-cloud` | Yes | - |
| `gemini-3.1-flash-lite-preview` | No | - |

### TCP

Promoted version: `20260408_161051`

| Version | Compiles | Packet parsing test |
| --- | --- | --- |
| `qwen3-coder:480b-cloud` | Yes | - |
| `gemini-3.1-flash-lite-preview` | Yes | - |

### IRC

Promoted version: `20260408_115044`

| Version | Compiles | Packet parsing test |
| --- | --- | --- |
| `qwen3-coder:480b-cloud` | Yes | - |
| `gemini-3.1-flash-lite-preview` | Yes | - |


### DHCP

Promoted version for `qwen3-coder:480b-cloud`: `20260408_002501` \
Promoted version for `gemini-3.1-flash-lite-preview`: `20260401_102200`

## 2026-04-07

Add release grammars: `ICMPv4`, `VXLAN`, `UDP` \
Changed release grammars: `BFD`, `DNS`

### Prompt tuning
Modify prompt to recommend using the `&convert` attribute for bitfields' fields.

### ICMPv4

Promoted version: `20260407_161854`

| Version | Compiles | Packet parsing test |
| --- | --- | --- |
| `qwen3-coder:480b-cloud` | Yes | - |
| `gemini-3.1-flash-lite-preview` | Yes | - |

### VXLAN

Promoted version: `20260407_161854`

| Version | Compiles | Packet parsing test |
| --- | --- | --- |
| `qwen3-coder:480b-cloud` | Yes | - |
| `gemini-3.1-flash-lite-preview` | Yes | - |

### UDP

Promoted version: `20260407_161854`

| Version | Compiles | Packet parsing test |
| --- | --- | --- |
| `qwen3-coder:480b-cloud` | Yes | - |
| `gemini-3.1-flash-lite-preview` | Yes | - |


### BFD

Promoted version for `qwen3-coder:480b-cloud`: `20260407_132922` \
Promoted version for `gemini-3.1-flash-lite-preview`: `20260407_135803`

| Version | Compiles | Packet parsing test |
| --- | --- | --- |
| `qwen3-coder:480b-cloud` | Yes | - |
| `gemini-3.1-flash-lite-preview` | Yes | - |

### DNS

Promoted version: `20260406_103054`

| Version | Compiles | Packet parsing test |
| --- | --- | --- |
| `qwen3-coder:480b-cloud` | Yes | - |
| `gemini-3.1-flash-lite-preview` | Yes | - |

## 2026-04-01

Changed release grammars: `SPP`, `TFTP`

### SPP

Promoted version: `20260401_114208`

| Version | Compiles | Packet parsing test |
| --- | --- | --- |
| `qwen3-coder:480b-cloud` | Yes | - |
| `gemini-3.1-flash-lite-preview` | Yes | - |

### TFTP

Promoted version: `20260401_102200`

| Version | Compiles | Packet parsing test |
| --- | --- | --- |
| `qwen3-coder:480b-cloud` | Yes | - |
| `gemini-3.1-flash-lite-preview` | Yes | - |

## 2026-03-31

Changed release grammars: `RSTP`, `LDAP`

### RSTP

Promoted version: `20260331_171341`

| Version | Compiles | Packet parsing test |
| --- | --- | --- |
| `qwen3-coder:480b-cloud` | Yes | - |
| `gemini-3.1-flash-lite-preview` | Yes | - |


### LDAP

Promoted version: `20260331_173440`

| Version | Compiles | Packet parsing test |
| --- | --- | --- |
| `qwen3-coder:480b-cloud` | No | - |
| `gemini-3.1-flash-lite-preview` | No | - |
