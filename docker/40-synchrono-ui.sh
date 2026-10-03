#!/bin/sh
# Builds the nginx site from env: one proxy location per service target,
# optional API key injection, optional basic auth, and /ui-config.json.
set -eu
set -f

SITE=/etc/nginx/conf.d/default.conf
EXTRA=/etc/nginx/synchrono-ui
WEB=/usr/share/nginx/html
TARGETS=${SERVICE_TARGETS:-}
KEYS=${SERVICE_API_KEYS:-}

fail() {
  echo "[config-ui] $*" >&2
  exit 1
}

trim() {
  printf '%s' "$1" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

[ -n "$TARGETS" ] || fail "SERVICE_TARGETS kosong. Contoh: SERVICE_TARGETS=DuckDB=http://192.168.2.107:7860"

mkdir -p "$EXTRA"
: > "$EXTRA/auth.conf"
auth=false
if [ -n "${UI_PASSWORD:-}" ]; then
  user=${UI_USER:-admin}
  printf '%s' "$user" | grep -Eq '^[A-Za-z0-9._-]+$' \
    || fail "UI_USER hanya boleh huruf, angka, titik, garis bawah, dan minus."
  printf '%s:{PLAIN}%s\n' "$user" "$UI_PASSWORD" > "$EXTRA/htpasswd"
  if chgrp nginx "$EXTRA/htpasswd" 2>/dev/null; then
    chmod 0640 "$EXTRA/htpasswd"
  else
    chmod 0644 "$EXTRA/htpasswd"
  fi
  printf 'auth_basic "Synchrono Config UI";\nauth_basic_user_file %s/htpasswd;\n' "$EXTRA" > "$EXTRA/auth.conf"
  auth=true
fi

cat > "$EXTRA/headers.conf" <<'EOF'
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "no-referrer" always;
EOF

resolver=$(awk '/^nameserver/ { print $2; exit }' /etc/resolv.conf 2>/dev/null || true)
case "$resolver" in
  *:*) resolver="[$resolver]" ;;
esac

key_count=$(printf '%s' "$KEYS" | awk -F, '{ print NF }')
key_count=${key_count:-0}
maps=""
locations=""
targets_json=""
injected_any=false
i=0

old_ifs=$IFS
IFS=','
for item in $TARGETS; do
  IFS=$old_ifs
  item=$(trim "$item")
  [ -n "$item" ] || continue

  case "$item" in
    http://*|https://*) name=""; url=$item ;;
    *=*) name=$(trim "${item%%=*}"); url=$(trim "${item#*=}") ;;
    *) fail "Target '$item' bukan URL http(s)." ;;
  esac
  url=${url%/}
  printf '%s' "$url" | grep -Eq '^https?://[][A-Za-z0-9.:_-]+$' \
    || fail "URL target '$url' harus berbentuk http://host:port tanpa path."
  scheme=${url%%://*}
  hostport=${url#*://}
  case "$hostport" in
    \[*\]:*) host="${hostport%]:*}]"; port=":${hostport##*:}" ;;
    \[*\]) host=$hostport; port="" ;;
    *:*:*) fail "Alamat IPv6 harus ditulis dalam kurung siku, mis. http://[::1]:7860" ;;
    *:*) host=${hostport%:*}; port=":${hostport##*:}" ;;
    *) host=$hostport; port="" ;;
  esac
  [ -n "$name" ] || name=$hostport
  printf '%s' "$name" | grep -Eq '^[][A-Za-z0-9 ._()/:-]+$' \
    || fail "Nama target '$name' hanya boleh huruf, angka, spasi, dan . _ ( ) / : -"

  key=""
  if [ "$key_count" -eq 1 ]; then
    key=$KEYS
  elif [ "$key_count" -gt 1 ]; then
    key=$(printf '%s' "$KEYS" | cut -d, -f"$((i + 1))")
  fi
  key=$(trim "$key")
  if [ -n "$key" ]; then
    printf '%s' "$key" | grep -Eq '^[A-Za-z0-9._~+/=-]+$' \
      || fail "API key target '$name' memuat karakter yang tidak didukung."
  fi

  # An IP literal or an /etc/hosts entry is used as is; any other name is
  # resolved per request, so a stopped service never stops the UI.
  ip=""
  if printf '%s' "$host" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$|^\['; then
    ip=$host
  else
    ip=$(awk -v h="$host" '!/^#/ { for (n = 2; n <= NF; n++) if ($n == h) { print $1; exit } }' /etc/hosts)
    case "$ip" in
      *:*) ip="[$ip]" ;;
    esac
  fi

  if [ -n "$ip" ]; then
    upstream="$scheme://$ip$port"
  else
    [ -n "$resolver" ] || fail "Tidak ada nameserver di /etc/resolv.conf untuk mencari '$host'."
    upstream="\$synchrono_upstream_$i"
  fi

  # `set` must come before `rewrite ... break`, which ends the rewrite phase.
  block="
    location /t/$i/api/v1/config/ {
        include $EXTRA/headers.conf;
        add_header Cache-Control \"no-store\" always;
        set \$synchrono_upstream_$i \"$url\";
        rewrite ^/t/$i(/.*)\$ \$1 break;
        proxy_http_version 1.1;
        proxy_connect_timeout 5s;
        proxy_read_timeout 120s;
        proxy_set_header Host \"$hostport\";
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;"
  if [ "$scheme" = https ]; then
    block="$block
        proxy_ssl_server_name on;
        proxy_ssl_name \"${host#[}\";"
  fi
  injected=false
  if [ -n "$key" ]; then
    maps="$maps
map \$http_x_api_key \$synchrono_key_$i {
    \"\"      \"$key\";
    default \$http_x_api_key;
}"
    block="$block
        proxy_set_header x-api-key \$synchrono_key_$i;"
    injected=true
    injected_any=true
  fi
  if [ -z "$ip" ]; then
    block="$block
        resolver $resolver valid=30s ipv6=off;"
  fi
  block="$block
        proxy_pass $upstream;
    }"
  locations="$locations
$block"

  [ -z "$targets_json" ] || targets_json="$targets_json,"
  targets_json="$targets_json
    {\"id\": \"$i\", \"name\": \"$name\", \"url\": \"$url\", \"base\": \"/t/$i\", \"keyInjected\": $injected}"
  if [ "$injected" = true ]; then
    echo "[config-ui] target $i: $name -> $url (API key diisi server)"
  else
    echo "[config-ui] target $i: $name -> $url (API key diisi di browser)"
  fi
  i=$((i + 1))
done
IFS=$old_ifs

[ "$i" -gt 0 ] || fail "SERVICE_TARGETS tidak memuat target yang sah."
# An injected key without a login would let anyone who reaches the port
# change the configuration, so that needs an explicit opt-in.
if [ "$injected_any" = true ] && [ "$auth" = false ]; then
  case "${UI_ALLOW_NO_LOGIN:-}" in
    1|true|ya|yes)
      echo "[config-ui] PERINGATAN: API key diisi server dan login UI mati (UI_ALLOW_NO_LOGIN);" \
           "siapa pun yang bisa membuka UI ini bisa mengubah konfigurasi." >&2 ;;
    *)
      fail "SERVICE_API_KEYS diisi tetapi UI_PASSWORD kosong: siapa pun yang membuka UI bisa mengubah konfigurasi. Isi UI_PASSWORD, atau set UI_ALLOW_NO_LOGIN=true untuk uji lokal." ;;
  esac
fi

cat > "$SITE" <<EOF
$maps

server {
    listen 80 default_server;
    server_name _;
    server_tokens off;
    root $WEB;
    client_max_body_size 1m;

    include $EXTRA/auth.conf;

    location = /ui-health {
        auth_basic off;
        access_log off;
        default_type text/plain;
        return 200 "ok\n";
    }

    location / {
        include $EXTRA/headers.conf;
        add_header Cache-Control "no-cache" always;
        add_header Content-Security-Policy "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'" always;
        try_files \$uri \$uri/ /index.html;
    }

    location /t/ {
        return 404;
    }
$locations
}
EOF

cat > "$WEB/ui-config.json" <<EOF
{
  "auth": $auth,
  "targets": [$targets_json
  ]
}
EOF

nginx -t -q || fail "Konfigurasi nginx yang dibangkitkan tidak sah (lihat pesan di atas)."
if [ "$auth" = true ]; then
  echo "[config-ui] siap: $i target, login UI aktif"
else
  echo "[config-ui] siap: $i target, login UI tidak aktif"
fi
