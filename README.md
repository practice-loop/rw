# qq-proxy — Railway 一键部署代理

单容器，一个端口同时提供 **HTTP 正向代理 / HTTPS CONNECT 隧道 / SOCKS5**，纯 Python 标准库。

默认端口 **9999**，Railway 通过 TCP Proxy 直接转发，裸 TCP 不终止 TLS，CONNECT 与 SOCKS5 都能穿。

## 部署

1. 把本目录推到 GitHub 仓库
2. Railway → **New Project → Deploy from GitHub repo** → 选这个仓库
3. 部署完成后 Networking → **+ TCP Proxy** → 端口填 `9999`
4. 得到 `xxxx.proxy.rlwy.net:yyyyy`，三个地址同时可用：

```
HTTP   : http://xxxx.proxy.rlwy.net:yyyyy
HTTPS  : http://xxxx.proxy.rlwy.net:yyyyy
SOCKS5 : socks5://xxxx.proxy.rlwy.net:yyyyy
```

一键模板按钮（模板仓库需先建好）：

```
https://railway.com/deploy?template=https://github.com/<你的账号>/qq-proxy
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `9999` | 代理监听端口，Railway 变量里也设 9999 |
| `TUNNEL_PORT` | `0` | 非 0 时同进程再开 WSS 隧道（配 Railway HTTPS 域名 `/tunnel`） |

## 验证

```sh
curl -x http://HOST:PORT http://example.com/            # 200
curl -x http://HOST:PORT https://www.baidu.com/         # 200，走 CONNECT
curl --socks5-hostname HOST:PORT https://api.github.com # 200
curl -x http://HOST:PORT https://api.ipify.org          # 出口 IP
curl http://HOST:PORT/health                            # {"ok":true}
```

## 自动开 TCP Proxy

```sh
export RAILWAY_TOKEN=xxx RAILWAY_ENVIRONMENT_ID=xxx RAILWAY_SERVICE_ID=xxx
bash enable_tcp.sh 9999
```

输出三行现成地址。API 字段不兼容时，在面板点 `+ TCP Proxy` 选 9999。

## 文件

所有文件平铺在同一目录：

```
server.py         入口：监听 PORT，线程池 accept
proxy_core.py     HTTP/HTTPS/SOCKS5 三合一（首字节分流）
tunnel_core.py    WSS 隧道服务端（TUNNEL_PORT 打开时启用）
Dockerfile        python:3.12-slim + HEALTHCHECK
railway.json      DOCKERFILE 构建 + /health 健康检查
deploy.sh         一键上线（railway CLI）
enable_tcp.sh     自动开 TCP Proxy 并打印地址
```

## 端口占用

```
9999  代理主端口（TCP Proxy 指向它）
8080  保留，未使用
```
