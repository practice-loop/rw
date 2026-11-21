#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单文件三合一代理：HTTP 正向代理(含 CONNECT 隧道) + SOCKS5(CONNECT)
用法: python3 multi_proxy.py [listen_host] [listen_port]
默认 0.0.0.0:8899
"""
import os, socket, struct, selectors, ssl, sys, threading, time
from urllib.parse import urlsplit

BUF = 65536
AUTH = None
_user = os.environ.get("PROXY_USER", "")
_pass = os.environ.get("PROXY_PASS", "")
if _user and _pass:
    import base64 as _b64
    AUTH = "Basic " + _b64.b64encode(("%s:%s" % (_user, _pass)).encode()).decode()
CONNECT_TIMEOUT = 15
IDLE_TIMEOUT = 300


def log(*a):
    print("[%s]" % time.strftime("%H:%M:%S"), *a, flush=True)


def tunnel(a, b):
    """双向字节泵，任一方向 EOF/出错即关闭。"""
    sel = selectors.DefaultSelector()
    a.setblocking(False); b.setblocking(False)
    sel.register(a, selectors.EVENT_READ, b)
    sel.register(b, selectors.EVENT_READ, a)
    last = time.time()
    try:
        while True:
            events = sel.select(timeout=IDLE_TIMEOUT)
            if not events:
                break
            for key, _ in events:
                src = key.fileobj
                dst = key.data
                try:
                    data = src.recv(BUF)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    data = b""
                if not data:
                    return
                last = time.time()
                try:
                    dst.sendall(data)
                except OSError:
                    return
    finally:
        sel.close()


def read_until(sock, token, limit=65536):
    buf = b""
    while token not in buf:
        chunk = sock.recv(BUF)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            break
    pre, _, post = buf.partition(token)
    return pre, post


def handle_socks5(conn, first):
    """first 为已读到的首字节 b'\x05'"""
    # 免认证协商
    nmethods = conn.recv(1)
    if not nmethods:
        return
    n = nmethods[0]
    methods = b""
    while len(methods) < n:
        c = conn.recv(n - len(methods))
        if not c:
            return
        methods += c
    if 0x00 not in methods:
        conn.sendall(b"\x05\xff")
        return
    conn.sendall(b"\x05\x00")

    # 请求
    hdr = b""
    while len(hdr) < 4:
        c = conn.recv(4 - len(hdr))
        if not c:
            return
        hdr += c
    ver, cmd, rsv, atyp = hdr
    if cmd != 0x01:                      # 仅 CONNECT
        conn.sendall(b"\x05\x07\x00\x01" + b"\x00" * 6)
        return
    if atyp == 0x01:
        addr = conn.recv(4)
        host = socket.inet_ntoa(addr)
    elif atyp == 0x03:
        ln = conn.recv(1)[0]
        host = conn.recv(ln).decode("idna" if False else "utf-8", "ignore")
    elif atyp == 0x04:
        addr = conn.recv(16)
        host = socket.inet_ntop(socket.AF_INET6, addr)
    else:
        conn.sendall(b"\x05\x08\x00\x01" + b"\x00" * 6)
        return
    port = struct.unpack("!H", conn.recv(2))[0]
    try:
        up = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
    except Exception as e:
        log("SOCKS5 fail", host, port, e)
        conn.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
        return
    conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
    log("SOCKS5 CONNECT %s:%d OK" % (host, port))
    tunnel(conn, up)
    up.close()


HOP_HEADERS = {b"proxy-connection", b"proxy-authorization", b"connection",
               b"keep-alive", b"te", b"trailers", b"upgrade"}


def handle_http(conn, first):
    head, rest = read_until(conn, b"\r\n\r\n")
    raw = first + head
    lines = raw.split(b"\r\n")
    if not lines or not lines[0]:
        return
    try:
        method, target, version = lines[0].split(b" ", 2)
    except ValueError:
        conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        return
    method_s = method.decode("latin-1").upper()

    if method_s != "CONNECT" and (target.startswith(b"/") or b"://" not in target):
        path_local = target.split(b"?")[0]
        if path_local in (b"/health", b"/"):
            body = b'{"ok":true}'
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
        else:
            conn.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        return

    if method_s == "CONNECT":
        if AUTH:
            got = None
            for ln in lines[1:]:
                k, _, v = ln.partition(b":")
                if k.strip().lower() == b"proxy-authorization":
                    got = v.strip().decode()
            if got != AUTH:
                conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                             b"Proxy-Authenticate: Basic realm=\"proxy\"\r\n"
                             b"Content-Length: 0\r\nConnection: close\r\n\r\n")
                return
        host, _, port = target.decode("latin-1").rpartition(":")
        port = int(port) if port.isdigit() else 443
        host = host.strip("[]")
        try:
            up = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except Exception as e:
            log("CONNECT fail", host, port, e)
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        log("CONNECT %s:%d OK" % (host, port))
        if rest:
            up.sendall(rest)
        tunnel(conn, up)
        up.close()
        return

    # 普通正向请求：target 必须是绝对 URI
    u = urlsplit(target.decode("latin-1"))
    if not u.hostname:
        conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        return
    scheme = (u.scheme or "http").lower()
    host = u.hostname
    port = u.port or (443 if scheme == "https" else 80)
    path = u.path or "/"
    if u.query:
        path += "?" + u.query

    if AUTH:
        got = None
        for ln in lines[1:]:
            k, _, v = ln.partition(b":")
            if k.strip().lower() == b"proxy-authorization":
                got = v.strip().decode()
        if got != AUTH:
            conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                         b"Proxy-Authenticate: Basic realm=\"proxy\"\r\n"
                         b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            return
    out = [b"%s %s HTTP/1.1" % (method, path.encode("latin-1"))]
    host_hdr_sent = False
    for ln in lines[1:]:
        if not ln:
            continue
        name = ln.split(b":", 1)[0].strip().lower()
        if name == b"host":
            host_hdr_sent = True
        if name in HOP_HEADERS:
            continue
        out.append(ln)
    if not host_hdr_sent:
        out.append(b"Host: " + (u.netloc.encode("latin-1")))
    out.append(b"Connection: close")
    payload = b"\r\n".join(out) + b"\r\n\r\n" + rest

    try:
        up = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        if scheme == "https":
            ctx = ssl.create_default_context()
            up = ctx.wrap_socket(up, server_hostname=host)
        up.sendall(payload)
    except Exception as e:
        log("HTTP upstream fail", host, port, e)
        conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        return
    log("HTTP %s %s://%s:%d%s" % (method_s, scheme, host, port, path))
    tunnel(conn, up)
    up.close()


def handle(conn, addr):
    try:
        conn.settimeout(30)
        first = conn.recv(1)
        if not first:
            return
        if first == b"\x05":
            handle_socks5(conn, first)
        else:
            handle_http(conn, first)
    except Exception as e:
        log("conn error", addr, repr(e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("PORT", "9999"))
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(256)
    log("multi_proxy listening on %s:%d (HTTP/CONNECT + SOCKS5)" % (host, port))
    while True:
        c, a = srv.accept()
        threading.Thread(target=handle, args=(c, a), daemon=True).start()


if __name__ == "__main__":
    main()
