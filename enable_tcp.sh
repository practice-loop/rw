#!/usr/bin/env bash
# 部署后自动为服务开 TCP Proxy，打印公网 host:port
# 需要: RAILWAY_TOKEN, RAILWAY_PROJECT_ID, RAILWAY_ENVIRONMENT_ID, RAILWAY_SERVICE_ID
set -euo pipefail
: "${RAILWAY_TOKEN:?export RAILWAY_TOKEN=<account token>}"
: "${RAILWAY_ENVIRONMENT_ID:?}"
: "${RAILWAY_SERVICE_ID:?}"
PORT="${1:-9999}"
read -r -d '' Q <<'GQL' || true
mutation($e:String!,$s:String!,$p:Int!){tcpProxyCreate(input:{environmentId:$e,serviceId:$s,applicationPort:$p}){domain proxyPort}}
GQL
RESP=$(curl -sS https://backboard.railway.app/graphql/v2   -H "Authorization: Bearer ${RAILWAY_TOKEN}"   -H "Content-Type: application/json"   -d "$(python3 -c "
import json,sys
q='''mutation(\$e:String!,\$s:String!,\$p:Int!){tcpProxyCreate(input:{environmentId:\$e,serviceId:\$s,applicationPort:\$p}){domain proxyPort}}'''
print(json.dumps({'query':q,'variables':{'e':sys.argv[1],'s':sys.argv[2],'p':int(sys.argv[3])}}))
" "$RAILWAY_ENVIRONMENT_ID" "$RAILWAY_SERVICE_ID" "$PORT")")
echo "$RESP"
python3 - "$RESP" <<'PY'
import json,sys
try:
    d=json.loads(sys.argv[1])
except Exception:
    sys.exit(0)
if d.get("errors"):
    print("API 报错，改在面板 Networking -> + TCP Proxy 里选端口 %s" % "9999")
    sys.exit(0)
r=(d.get("data") or {}).get("tcpProxyCreate") or {}
if r.get("domain"):
    h,p=r["domain"],r.get("proxyPort")
    print("")
    print("HTTP   : http://%s:%s" % (h,p))
    print("HTTPS  : http://%s:%s" % (h,p))
    print("SOCKS5 : socks5://%s:%s" % (h,p))
PY
