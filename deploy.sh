#!/usr/bin/env bash
# 本机一键上线（需已安装 railway CLI 并 login）
set -euo pipefail
cd "$(dirname "$0")/.."
railway init --name qq-proxy 2>/dev/null || true
railway variables --set PORT=9999
railway up --detach
railway domain 2>/dev/null || true
echo "下一步: railway status 看部署，或在面板 + TCP Proxy 选 9999"
