"""Frame identity survives filtering during payload extraction."""

import hashlib

from scapy.layers.dns import DNS
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether
from scapy.packet import Raw
from scapy.utils import wrpcap

from scripts import extract_layer


def test_extraction_records_original_frame_numbers(tmp_path):
    capture = tmp_path / "mixed.pcap"
    packets = [
        Ether() / IP() / UDP(sport=50000, dport=53) / DNS(id=1),
        Ether() / IP() / UDP(sport=50000, dport=9000) / Raw(b"not dns"),
        Ether() / IP() / UDP(sport=53, dport=50000) / DNS(id=2),
    ]
    wrpcap(str(capture), packets)
    output = tmp_path / "payloads"
    output.mkdir()
    handler = extract_layer.resolve_protocol("dns")

    records = extract_layer.extract_packets_from_capture(str(capture), 0, handler, str(output))

    assert [record["frame_number"] for record in records] == [1, 3]
    assert [record["packet_index"] for record in records] == [0, 1]
    for record in records:
        packet_bytes = (output / record["packet_path"].split("/")[-1]).read_bytes()
        assert hashlib.sha256(packet_bytes).hexdigest() == record["sha256"]
