#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自写 WSS 隧道服务端：监听 15849，被 Railway edge 用 443 反代（TLS 在 edge 终止，回源明文 HTTP/1.1）。
隧道协议：WS 二进制帧，首帧 JSON {"cmd":"connect","host":H,"port":P}，服务端回 {"ok":true}，
随后双向裸字节各走一帧。纯标准库，无第三方依赖。"""
import socket, ssl, base64, hashlib, json, struct, threading, sys, os, time

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
PATH = os.environ.get("TUNNEL_PATH", "/tunnel")
DL_DIR = os.environ.get("TUNNEL_DL_DIR", "/app/working/dl")
BUFSIZE = 32768


def log(*a):
    print("[srv %s]" % time.strftime("%H:%M:%S"), *a, flush=True)


def ws_send(sock, payload, opcode=0x2):
    n = len(payload)
    if n < 126:
        hdr = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 65536:
        hdr = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        hdr = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    sock.sendall(hdr + payload)


def ws_frame(buf):
    """从 bytearray 取一个完整帧，不够返回 None"""
    if len(buf) < 2:
        return None
    b1, b2 = buf[0], buf[1]
    opcode = b1 & 0x0F
    masked = b2 & 0x80
    ln = b2 & 0x7F
    off = 2
    if ln == 126:
        if len(buf) < 4:
            return None
        ln = struct.unpack("!H", bytes(buf[2:4]))[0]
        off = 4
    elif ln == 127:
        if len(buf) < 10:
            return None
        ln = struct.unpack("!Q", bytes(buf[2:10]))[0]
        off = 10
    if masked:
        if len(buf) < off + 4:
            return None
        mkey = bytes(buf[off:off + 4])
        off += 4
    if len(buf) < off + ln:
        return None
    payload = bytes(buf[off:off + ln])
    if masked:
        payload = bytes(payload[i] ^ mkey[i % 4] for i in range(ln))
    del buf[:off + ln]
    return opcode, payload


def read_msg(conn, buf):
    while True:
        f = ws_frame(buf)
        if f is not None:
            op, pl = f
            if op == 0x8:
                return None
            if op == 0x9:
                try:
                    ws_send(conn, pl, 0xA)
                except OSError:
                    return None
                continue
            if op in (0x1, 0x2):
                return pl
            continue
        try:
            c = conn.recv(BUFSIZE)
        except (socket.timeout, OSError):
            return None
        if not c:
            return None
        buf += c


def read_http_head(conn, buf):
    while b"\r\n\r\n" not in buf:
        try:
            c = conn.recv(BUFSIZE)
        except OSError:
            return None
        if not c:
            return None
        buf += c
        if len(buf) > 32768:
            return None
    head, _, rest = bytes(buf).partition(b"\r\n\r\n")
    del buf[:len(head) + 4]
    lines = head.split(b"\r\n")
    hdrs = {}
    for ln in lines[1:]:
        if b":" in ln:
            k, _, v = ln.partition(b":")
            hdrs[k.strip().lower().decode("latin-1")] = v.strip().decode("latin-1")
    return lines[0].decode("latin-1", "ignore"), hdrs


def plain(conn, code, reason, body=b""):
    try:
        conn.sendall(b"HTTP/1.1 %d %s\r\nContent-Type: text/plain\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" %
                     (code, reason.encode(), len(body)) + body)
    except OSError:
        pass


def handle(conn, addr):
    buf = bytearray()
    up = None
    try:
        conn.settimeout(45)
        got = read_http_head(conn, buf)
        if not got:
            return
        reqline, hdrs = got
        parts = reqline.split(" ")
        path = parts[1] if len(parts) > 1 else "/"
        if path.split("?")[0].startswith("/dl/"):
            fn = os.path.basename(path.split("?")[0][4:])
            fp = os.path.join(DL_DIR, fn)
            if not os.path.isfile(fp):
                plain(conn, 404, "Not Found", b"no such file")
                return
            size = os.path.getsize(fp)
            conn.sendall(("HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
                          "Content-Disposition: attachment; filename=\"%s\"\r\n"
                          "Content-Length: %d\r\nConnection: close\r\n\r\n" % (fn, size)).encode())
            with open(fp, "rb") as f:
                while True:
                    c = f.read(65536)
                    if not c:
                        break
                    conn.sendall(c)
            log("DL", fn, size)
            return
        if path.split("?")[0] == "/health":
            plain(conn, 200, "OK", b"ok")
            return
        if path.split("?")[0] != PATH or hdrs.get("upgrade", "").lower() != "websocket":
            plain(conn, 404, "Not Found", b"tunnel endpoint: " + PATH.encode())
            return
        key = hdrs.get("sec-websocket-key", "")
        if not key:
            plain(conn, 400, "Bad Request", b"missing key")
            return
        acc = base64.b64encode(hashlib.sha1(key.encode() + GUID).digest()).decode()
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\n"
                     b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: " + acc.encode() + b"\r\n\r\n")
        log("WS UPGRADE", addr[0], reqline)

        conn.settimeout(120)
        first = read_msg(conn, buf)
        if not first:
            return
        try:
            spec = json.loads(first.decode("utf-8", "ignore"))
        except Exception:
            ws_send(conn, b'{"ok":false,"msg":"bad json"}')
            return
        if spec.get("cmd") != "connect":
            ws_send(conn, b'{"ok":false,"msg":"unsupported cmd"}')
            return
        host = spec.get("host")
        port = int(spec.get("port", 0))
        try:
            up = socket.create_connection((host, port), timeout=15)
        except Exception as e:
            ws_send(conn, json.dumps({"ok": False, "msg": str(e)}).encode())
            log("CONNECT FAIL", host, port, e)
            return
        ws_send(conn, b'{"ok":true}')
        log("TUNNEL -> %s:%d" % (host, port))
        conn.settimeout(None)

        stop = threading.Event()

        def pump_up():
            try:
                while not stop.is_set():
                    d = up.recv(BUFSIZE)
                    if not d:
                        break
                    ws_send(conn, d)
            except OSError:
                pass
            finally:
                stop.set()
                try:
                    ws_send(conn, b"", 0x8)
                except OSError:
                    pass

        t = threading.Thread(target=pump_up, daemon=True)
        t.start()
        while not stop.is_set():
            m = read_msg(conn, buf)
            if m is None:
                break
            try:
                up.sendall(m)
            except OSError:
                break
        stop.set()
        t.join(timeout=2)
    except Exception as e:
        log("ERR", addr, repr(e))
    finally:
        for s in (up, conn):
            try:
                s and s.close()
            except OSError:
                pass


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 15849
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(512)
    log("tunnel_server on %s:%d  path=%s" % (host, port, PATH))
    while True:
        c, a = srv.accept()
        threading.Thread(target=handle, args=(c, a), daemon=True).start()


if __name__ == "__main__":
    main()
