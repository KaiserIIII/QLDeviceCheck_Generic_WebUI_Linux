"""Bounded stream assembly; TCP recv boundaries are not application frames."""
import socket
import time


def modbus_tcp_complete(data):
    if len(data) < 6:
        return False
    length = int.from_bytes(data[4:6], 'big')
    return length < 3 or length > 254 or len(data) >= 6 + length


def receive_response(sock, timeout, max_bytes=4096, complete=None):
    deadline = time.monotonic() + timeout
    data = bytearray()
    while len(data) < max_bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock.settimeout(remaining)
        try:
            fragment = sock.recv(max_bytes - len(data))
        except socket.timeout:
            break
        except ConnectionError:
            if not data:
                raise
            break
        if not fragment:
            break
        data.extend(fragment)
        if complete and complete(bytes(data)):
            break
    return bytes(data)
