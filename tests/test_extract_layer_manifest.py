"""Frame identity survives filtering during payload extraction."""

import hashlib
from pathlib import Path

from scapy.layers.dns import DNS
from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.inet6 import IPv6
from scapy.layers.l2 import Ether
from scapy.packet import Raw
from scapy.utils import PcapReader, wrpcap

from scripts import extract_layer


RAW_DATA = Path(__file__).resolve().parents[1] / "data" / "raw"


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


def test_babel_extraction_keeps_complete_udp_datagrams(tmp_path):
    capture = tmp_path / "babel.pcap"
    hello = (RAW_DATA / "babel/positive/hello.bin").read_bytes()
    update = (RAW_DATA / "babel/positive/router-update.bin").read_bytes()
    with_trailer = hello + b"\x00"
    packets = [
        Ether() / IP() / UDP(sport=6696, dport=6696) / Raw(hello),
        Ether() / IPv6() / UDP(sport=6696, dport=6696) / Raw(update),
        Ether() / IP() / UDP(sport=50000, dport=9000) / Raw(with_trailer),
        Ether() / IP() / UDP(sport=6696, dport=6696) / Raw(b"\x2a\x03\x00\x00"),
        Ether() / IP() / UDP(sport=6696, dport=6696) / Raw(b"\x2a\x02"),
        Ether() / IP() / TCP(sport=6696, dport=6696) / Raw(hello),
    ]
    wrpcap(str(capture), packets)
    output = tmp_path / "payloads"
    output.mkdir()

    records = extract_layer.extract_packets_from_capture(
        str(capture), 0, extract_layer.resolve_protocol("babel"), str(output)
    )

    assert [record["frame_number"] for record in records] == [1, 2, 3]
    assert [(output / Path(record["packet_path"]).name).read_bytes() for record in records] == [
        hello,
        update,
        with_trailer,
    ]


def test_bfd_extraction_keeps_control_packet_bytes(tmp_path):
    from scapy.contrib.bfd import BFD

    capture = tmp_path / "bfd.pcap"
    plain = (RAW_DATA / "bfd/positive/no-diagnostic.bin").read_bytes()
    authenticated = (RAW_DATA / "bfd/positive/no-diagnostic-md5-auth.bin").read_bytes()
    packets = [
        Ether() / IP() / UDP(sport=50000, dport=3784) / Raw(plain),
        Ether() / IPv6() / UDP(sport=50001, dport=4784) / Raw(authenticated),
        Ether() / IP() / UDP(sport=50002, dport=9000) / Raw(plain),
        Ether() / IP() / TCP(sport=50003, dport=3784) / Raw(plain),
    ]
    wrpcap(str(capture), packets)
    output = tmp_path / "payloads"
    output.mkdir()

    records = extract_layer.extract_packets_from_capture(
        str(capture), 0, extract_layer.resolve_protocol("bfd"), str(output)
    )

    assert [record["frame_number"] for record in records] == [1, 2]
    assert [(output / Path(record["packet_path"]).name).read_bytes() for record in records] == [
        plain,
        authenticated,
    ]
    with PcapReader(str(capture)) as reader:
        assert next(iter(reader)).haslayer(BFD)


def test_bgp_extraction_keeps_original_tcp_payloads(tmp_path):
    from scapy.contrib.bgp import BGPHeader

    capture = tmp_path / "bgp.pcap"
    opened = (RAW_DATA / "bgp/positive/open.bin").read_bytes()
    keepalive = (RAW_DATA / "bgp/positive/keepalive.bin").read_bytes()
    update = (RAW_DATA / "bgp/positive/update.bin").read_bytes()
    packets = [
        Ether() / IP() / TCP(sport=50000, dport=179, flags="PA") / Raw(opened),
        Ether() / IP() / TCP(sport=179, dport=50000, flags="A"),
        Ether() / IP() / TCP(sport=179, dport=50000, flags="PA") / Raw(keepalive),
        Ether() / IP() / TCP(sport=50001, dport=9000, flags="PA") / Raw(update),
        Ether() / IP() / TCP(sport=50002, dport=179, flags="PA") / Raw(b"not bgp"),
        Ether() / IP() / TCP(sport=50003, dport=179, flags="PA") / Raw(b"x" + keepalive),
        Ether() / IP() / TCP(sport=50004, dport=179, flags="PA") / Raw(b"\xff" * 16 + b"\x00\x12\x04"),
        Ether() / IP() / UDP(sport=50005, dport=179) / Raw(keepalive),
    ]
    wrpcap(str(capture), packets)
    with PcapReader(str(capture)) as reader:
        assert next(iter(reader)).haslayer(BGPHeader)
    output = tmp_path / "payloads"
    output.mkdir()

    records = extract_layer.extract_packets_from_capture(
        str(capture), 0, extract_layer.resolve_protocol("bgp"), str(output)
    )

    assert [record["frame_number"] for record in records] == [1, 3, 4]
    assert [(output / Path(record["packet_path"]).name).read_bytes() for record in records] == [
        opened,
        keepalive,
        update,
    ]


def test_rtp_extraction_preserves_valid_udp_payloads(tmp_path):
    capture = tmp_path / "rtp.pcap"
    audio = (RAW_DATA / "rtp/positive/ITU-T-G711-seq-1.bin").read_bytes()
    video = (RAW_DATA / "rtp/positive/ITU-T-H263-seq-53966.bin").read_bytes()
    extended = (
        b"\xb1\x60" + audio[2:12] + b"\x12\x34\x56\x78"
        + b"\xbe\xde\x00\x01" + b"\x10\x00\x00\x00" + b"data\x00\x02"
    )
    rtcp_sender_report = b"\x80\xc8\x00\x06" + b"\x00" * 24
    rtcp_feedback = b"\x80\xcd\x00\x03" + b"\x00" * 12
    packets = [
        Ether() / IP() / UDP(sport=5004, dport=5004) / Raw(audio),
        Ether() / IPv6() / UDP(sport=5006, dport=9000) / Raw(video),
        Ether() / IP() / UDP(sport=5008, dport=5008) / Raw(extended),
        Ether() / IP() / UDP(sport=5005, dport=5005) / Raw(rtcp_sender_report),
        Ether() / IP() / UDP(sport=5005, dport=5005) / Raw(rtcp_feedback),
        Ether() / IP() / UDP(sport=53, dport=50000) / DNS(id=0x8001, qr=1),
        Ether() / IP() / TCP(sport=5004, dport=5004) / Raw(audio),
    ]
    packets.extend(
        Ether() / IP() / UDP(sport=5010, dport=5010) / Raw(path.read_bytes())
        for path in sorted((RAW_DATA / "rtp/negative").glob("*.bin"))
    )
    wrpcap(str(capture), packets)
    output = tmp_path / "payloads"
    output.mkdir()

    records = extract_layer.extract_packets_from_capture(
        str(capture), 0, extract_layer.resolve_protocol("rtp"), str(output)
    )

    assert [record["frame_number"] for record in records] == [1, 2, 3]
    assert [(output / Path(record["packet_path"]).name).read_bytes() for record in records] == [
        audio,
        video,
        extended,
    ]
