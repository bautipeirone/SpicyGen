"""Frame identity survives filtering during payload extraction."""

import hashlib

from scapy.layers.dns import DNS
from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import Ether
from scapy.packet import Raw
from scapy.utils import PcapReader, wrpcap

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


def test_http_extraction_keeps_complete_tcp_payloads(tmp_path):
    # Port 80 traffic is decoded into Scapy HTTP layers once this is loaded.
    from scapy.layers import http

    capture = tmp_path / "http.pcap"
    request = b"GET / HTTP/1.1\r\nHost: example.test\r\n\r\n"
    response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"
    off_port_request = b"POST /api HTTP/1.1\r\nHost: example.test\r\n\r\n"
    packets = [
        Ether() / IP() / TCP(sport=50000, dport=80, flags="PA") / Raw(request),
        Ether() / IP() / TCP(sport=80, dport=50000, flags="A"),
        Ether() / IP() / TCP(sport=80, dport=50000, flags="PA") / Raw(response),
        Ether() / IP() / TCP(sport=50001, dport=8080, flags="PA") / Raw(off_port_request),
        Ether() / IP() / TCP(sport=50002, dport=9000, flags="PA") / Raw(b"hello HTTP/1.1 world"),
        Ether() / IP() / UDP(sport=50003, dport=9000) / Raw(request),
    ]
    wrpcap(str(capture), packets)
    with PcapReader(str(capture)) as reader:
        assert next(iter(reader)).haslayer(http.HTTP)
    output = tmp_path / "payloads"
    output.mkdir()

    records = extract_layer.extract_packets_from_capture(
        str(capture), 0, extract_layer.resolve_protocol("http"), str(output)
    )

    assert [record["frame_number"] for record in records] == [1, 3, 4]
    assert [record["packet_index"] for record in records] == [0, 1, 2]
    assert [(output / record["packet_path"].split("/")[-1]).read_bytes() for record in records] == [
        request,
        response,
        off_port_request,
    ]


def test_rtsp_extraction_keeps_complete_tcp_payloads(tmp_path):
    from scapy.contrib.rtsp import RTSP

    capture = tmp_path / "rtsp.pcap"
    request = b"DESCRIBE rtsp://example.test/movie RTSP/1.0\r\nCSeq: 1\r\n\r\n"
    response = b"RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Length: 4\r\n\r\nbody"
    off_port_request = b"PLAY rtsp://example.test/movie RTSP/2.0\r\nCSeq: 2\r\n\r\n"
    packets = [
        Ether() / IP() / TCP(sport=50000, dport=554, flags="PA") / Raw(request),
        Ether() / IP() / TCP(sport=554, dport=50000, flags="A"),
        Ether() / IP() / TCP(sport=554, dport=50000, flags="PA") / Raw(response),
        Ether() / IP() / TCP(sport=50001, dport=8554, flags="PA") / Raw(off_port_request),
        Ether() / IP() / TCP(sport=554, dport=50000, flags="PA") / Raw(b"$\x00\x00\x04data"),
        Ether() / IP() / TCP(sport=50002, dport=9000, flags="PA") / Raw(b"hello RTSP/1.0 world"),
        Ether() / IP() / UDP(sport=50003, dport=9000) / Raw(request),
    ]
    wrpcap(str(capture), packets)
    with PcapReader(str(capture)) as reader:
        assert next(iter(reader)).haslayer(RTSP)
    output = tmp_path / "payloads"
    output.mkdir()

    records = extract_layer.extract_packets_from_capture(
        str(capture), 0, extract_layer.resolve_protocol("rtsp"), str(output)
    )

    assert [record["frame_number"] for record in records] == [1, 3, 4]
    assert [record["packet_index"] for record in records] == [0, 1, 2]
    assert [(output / record["packet_path"].split("/")[-1]).read_bytes() for record in records] == [
        request,
        response,
        off_port_request,
    ]
