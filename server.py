#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Railway 入口：一个端口同时提供 HTTP 正向代理 / CONNECT 隧道 / SOCKS5。
环境变量：
  PORT         代理端口，默认 9999
  PROXY_USER   可选，Basic 认证用户名
  PROXY_PASS   可选，Basic 认证密码
  TUNNEL_PORT  可选，非 0 时同时开 WSS 隧道（配合 Railway HTTPS 域名）
"""
import os, socket, threading, time
import proxy_core, tunnel_core


def log(*a):
    print("[%s]" % time.strftime("%H:%M:%S"), *a, flush=True)


def listen(port, host="0.0.0.0"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(512)
    return s


def accept_loop(sock, handler, tag):
    while True:
        c, a = sock.accept()
        threading.Thread(target=handler, args=(c, a), daemon=True).start()


def main():
    port = int(os.environ.get("PORT", "9999"))
    tport = int(os.environ.get("TUNNEL_PORT", "0"))
    threading.Thread(target=accept_loop, args=(listen(port), proxy_core.handle, "proxy"), daemon=True).start()
    log("proxy listening 0.0.0.0:%d  (HTTP / CONNECT / SOCKS5, auth=%s)" %
        (port, "on" if proxy_core.AUTH else "off"))
    if tport:
        threading.Thread(target=accept_loop, args=(listen(tport), tunnel_core.handle, "tunnel"), daemon=True).start()
        log("wss tunnel listening 0.0.0.0:%d  path=%s" % (tport, tunnel_core.PATH))
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
