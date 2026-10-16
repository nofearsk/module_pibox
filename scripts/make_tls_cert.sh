#!/bin/bash
# Our own CA + server certificate for https:// and wss:// on this Pi
# (same ports as http:// and ws://, see services/dual_tls.py).
#
#   sudo scripts/make_tls_cert.sh                  # create CA (once) + server cert
#   sudo scripts/make_tls_cert.sh --check --restart  # daily cron: reissue the server
#        cert if it expires within 30 days or no longer covers this Pi's IPs
#
# The CA is made once and kept, so devices that installed it (download from
# http://<pi>:8080/tls/ca.crt) stay valid when the server cert is reissued.
# The CA is name-constrained to private IPs and this Pi's names, so a device
# that trusts it cannot be fooled for any public website.
# Extra DNS names: PIBOX_TLS_NAMES="gate.example.lan other.lan" (set before the
# CA is first made, or they fall outside its constraints).
set -euo pipefail

TLS_DIR="${PIBOX_TLS_DIR:-/var/pibox/tls}"
SERVER_DAYS=825   # Apple's limit for server certs, private CAs included
CA_DAYS=3650
CHECK=0; RESTART=0
for a in "$@"; do
  case "$a" in
    --check) CHECK=1 ;;
    --restart) RESTART=1 ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done

HOST="$(hostname | tr 'A-Z' 'a-z')"
IPS="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)[0-9.]+$' || true)"  # private only: the CA allows nothing else
NAMES="$HOST $HOST.local localhost ${PIBOX_TLS_NAMES:-}"

mkdir -p "$TLS_DIR"; chmod 755 "$TLS_DIR"
cd "$TLS_DIR"

needs_cert() {
  [ -f server.crt ] && [ -f server.key ] || return 0
  openssl x509 -in server.crt -noout -checkend $((30*86400)) >/dev/null || return 0
  local san; san="$(openssl x509 -in server.crt -noout -ext subjectAltName 2>/dev/null)"
  for ip in $IPS 127.0.0.1; do
    echo "$san" | grep -q "IP Address:$ip\(,\|$\)" || return 0
  done
  return 1
}

if [ "$CHECK" = 1 ] && ! needs_cert; then
  exit 0
fi

if [ ! -f ca.key ] || [ ! -f ca.crt ]; then
  echo "Creating CA in $TLS_DIR"
  NC="permitted;IP:10.0.0.0/255.0.0.0,permitted;IP:172.16.0.0/255.240.0.0"
  NC="$NC,permitted;IP:192.168.0.0/255.255.0.0,permitted;IP:127.0.0.0/255.0.0.0"
  for n in $NAMES local; do NC="$NC,permitted;DNS:$n"; done
  (umask 077; openssl genrsa -out ca.key 3072 2>/dev/null)
  openssl req -x509 -new -key ca.key -sha256 -days "$CA_DAYS" \
    -subj "/O=PiBox/CN=PiBox Local CA ($HOST)" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" \
    -addext "nameConstraints=critical,$NC" \
    -out ca.crt
fi

SAN=""
for ip in $IPS 127.0.0.1; do SAN="$SAN,IP:$ip"; done
for n in $NAMES; do SAN="$SAN,DNS:$n"; done
SAN="${SAN#,}"

echo "Issuing server cert for: $SAN"
cat > server.ext <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
subjectAltName=$SAN
EOF
(umask 077; openssl genrsa -out server.key.new 2048 2>/dev/null)
openssl req -new -key server.key.new -subj "/O=PiBox/CN=$HOST" -out server.csr
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -sha256 -days "$SERVER_DAYS" -extfile server.ext -out server.crt.new 2>/dev/null
mv server.key.new server.key; mv server.crt.new server.crt
rm -f server.csr server.ext
chmod 600 ca.key server.key; chmod 644 ca.crt server.crt

openssl verify -CAfile ca.crt server.crt
if [ "$RESTART" = 1 ]; then systemctl restart pibox; fi
