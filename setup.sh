#!/usr/bin/env bash
#
# Caprica region setup wizard (Linux)
#
# Walks you through the configuration step by step and fills in:
#   - bin/Regions/Regions.ini        (region name, UUID, location, ports, address)
#   - bin/OpenSim.ini                (http_listener_port, service protocol scheme)
#   - bin/config-include/GridCommon.ini (optional MySQL storage)
#
# Safe to re-run: every modified file is backed up first (*.bak-YYYYMMDD-HHMMSS).
#
set -euo pipefail

BINDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/bin" && pwd)"
OSINI="$BINDIR/OpenSim.ini"
GRIDCOMMON="$BINDIR/config-include/GridCommon.ini"
REGIONS_INI="$BINDIR/Regions/Regions.ini"

C_YES="https"
C_NO="http"

say()  { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
warn() { printf '\033[1;33m   ! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

ask() { # ask "question" "default" -> answer
    local q="$1" def="${2:-}" a=""
    if [ -n "$def" ]; then
        read -r -p "   $q [$def]: " a || true
    else
        read -r -p "   $q: " a || true
    fi
    printf '%s' "${a:-$def}"
}

ask_yn() { # ask_yn "question" "Y/n" -> yes|no
    local q="$1" def="${2:-Y}" a
    a="$(ask "$q [$def]" "$def")"
    case "$(printf '%s' "$a" | tr 'A-Z' 'a-z')" in
        y|yes) printf 'yes' ;;
        *)     printf 'no'  ;;
    esac
}

backup() {
    local f="$1"
    [ -f "$f" ] || return 0
    local b="${f}.bak-$(date +%Y%m%d-%H%M%S)"
    cp -p "$f" "$b"
    info "backup: $b"
}

set_ini_value() { # set_ini_value FILE KEY VALUE  (uncomments and sets "    KEY = VALUE")
    local f="$1" key="$2" val="$3"
    if grep -qE "^[[:space:]]*;*[[:space:]]*${key}[[:space:]]*=" "$f"; then
        sed -i -E "s|^[[:space:]]*;*[[:space:]]*${key}[[:space:]]*=.*|    ${key} = ${val}|" "$f"
    else
        printf '\n    %s = %s\n' "$key" "$val" >> "$f"
        warn "$key was not found in $f - appended at the end (check section!)"
    fi
}

port_free() { # returns 0 if nothing listens on TCP port
    ! ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${1}\$"
}

# ---------------------------------------------------------------------------
say "0/7 Preflight checks"

[ -d "$BINDIR" ] || die "bin/ directory not found next to this script ($BINDIR)"
[ -f "$OSINI" ]    || die "OpenSim.ini not found: $OSINI"
[ -f "$REGIONS_INI" ] || die "Regions.ini not found: $REGIONS_INI"

for cmd in mono screen; do
    command -v "$cmd" >/dev/null 2>&1 || warn "'$cmd' not found in PATH - install it before starting the region"
done
command -v ufw >/dev/null 2>&1 || warn "ufw not found - configure your firewall manually"

info "Working directory: $BINDIR"

# ---------------------------------------------------------------------------
say "1/7 Region identity"

REGION_NAME="$(ask "Region name (shown on the map, e.g. MyRegion)" "")"
[ -n "$REGION_NAME" ] || die "region name is required"
printf '%s' "$REGION_NAME" | grep -qE '^[A-Za-z0-9_ -]+$' || die "region name may contain only letters, digits, space, underscore, dash"

DEFAULT_UUID="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || uuidgen 2>/dev/null || true)"
REGION_UUID="$(ask "Region UUID (generate: https://gridlist.ru/uuid/)" "$DEFAULT_UUID")"
printf '%s' "$REGION_UUID" | grep -qiE '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' \
    || die "invalid UUID format: $REGION_UUID"

LOC_XY="$(ask "Region location X,Y (check https://caprica.xyz/simple-map/ first)" "1000,1000")"
printf '%s' "$LOC_XY" | grep -qE '^[0-9]+,[0-9]+$' || die "location must be X,Y integers, got: $LOC_XY"

SIZE_XY="$(ask "Region size X,Y (256, 512 or 1024)" "256,256")"
printf '%s' "$SIZE_XY" | grep -qE '^[0-9]+,[0-9]+$' || die "size must be X,Y integers, got: $SIZE_XY"
read -r SIZE_X SIZE_Y <<<"$(printf '%s' "$SIZE_XY" | tr ',' ' ')"
[ "$SIZE_X" = "$SIZE_Y" ] || warn "non-square region (${SIZE_X}x${SIZE_Y}) - supported, but unusual"
case "$SIZE_X" in 256|512|1024) : ;; *) warn "size ${SIZE_X}x${SIZE_Y} is unusual (256/512/1024 are typical)" ;; esac

MAX_PRIMS="$(ask "MaxPrims" "25000")"
printf '%s' "$MAX_PRIMS" | grep -qE '^[0-9]+$' || die "MaxPrims must be a number"

# ---------------------------------------------------------------------------
say "2/7 Network: listener port and external address"

DEF_PORT="9022"
PORT="$(ask "Region client port (http_listener_port = InternalPort, must be open inbound)" "$DEF_PORT")"
printf '%s' "$PORT" | grep -qE '^[0-9]+$' || die "port must be a number"
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "port out of range"
if port_free "$PORT"; then
    info "port $PORT is free"
else
    warn "something already listens on port $PORT (ss -ltn) - pick another one"
    PORT="$(ask "Pick another port" "$((PORT+1))")"
fi

EXTERNAL_HOST="$(ask "External host name or IP of this server (as visitors will reach it)" "")"
[ -n "$EXTERNAL_HOST" ] || die "external host is required"

# ---------------------------------------------------------------------------
say "3/7 Protocol to Caprica services"

info "Caprica public services (port 9000) are HTTPS-only since Nov 2026."
info "HTTPS is strongly recommended: it encrypts your login, session and asset traffic."
if ask_yn "Use HTTPS for Caprica public services?" "Y" | grep -q yes; then
    BASE_SCHEME="https"
else
    BASE_SCHEME="http"
    warn "http selected - it will only work if Caprica re-enables plain HTTP on port 9000"
fi

PRIV_SCHEME="http"
if ask_yn "Caprica PRIVATE services use plain http on port 8003 today. Keep http?" "Y" | grep -q yes; then
    PRIV_SCHEME="http"
else
    PRIV_SCHEME="https"
    warn "PrivScheme=https - only correct if Caprica support confirmed TLS on port 8003"
fi

# ---------------------------------------------------------------------------
say "4/7 Writing Regions.ini"

backup "$REGIONS_INI"
cat > "$REGIONS_INI" <<EOF
[${REGION_NAME}]
RegionUUID = "${REGION_UUID}"
Location = "${LOC_XY}"

SizeX = ${SIZE_X}
SizeY = ${SIZE_Y}

MaxPrims = ${MAX_PRIMS}

; don't touch this.
InternalAddress = "0.0.0.0"

; Region client port - must match http_listener_port in OpenSim.ini
InternalPort = ${PORT}


AllowAlternatePorts = False

; External address visitors use to reach this region.
ExternalHostName = "${EXTERNAL_HOST}"
EOF
info "written: $REGIONS_INI"

# ---------------------------------------------------------------------------
say "5/7 Patching OpenSim.ini"

backup "$OSINI"

set_ini_value "$OSINI" "http_listener_port" "${PORT}"
set_ini_value "$OSINI" "BaseScheme" "\"${BASE_SCHEME}\""
set_ini_value "$OSINI" "PrivScheme" "\"${PRIV_SCHEME}\""

# sanity: show the resolved constants
info "OpenSim.ini now contains:"
grep -nE '^\s*(BaseScheme|PrivScheme|BaseURL|PrivURL|http_listener_port)' "$OSINI" | sed 's/^/      /'

# ---------------------------------------------------------------------------
say "6/7 Database storage"

info "SQLite (default): zero setup - database files (*.db) are created"
info "inside the region directory (bin/). Offline IM is DISABLED: the"
info "bundled SQLite provider has no OfflineIM backend."
info "MySQL (optional): enables offline IM - requires a MySQL server."
info "  Install: sudo apt install mysql-server"
info "  Then create db+user, e.g.:"
info "    CREATE DATABASE opensim; CREATE USER 'opensim'@'localhost' IDENTIFIED BY '...';"
info "    GRANT ALL PRIVILEGES ON opensim.* TO 'opensim'@'localhost';"

if ask_yn "Use MySQL storage? (required for offline IM)" "n" | grep -q yes; then
    DB_HOST="$(ask "MySQL host" "127.0.0.1")"
    DB_NAME="$(ask "Database name" "opensim")"
    DB_USER="$(ask "MySQL user" "opensim")"
    DB_PASS="$(ask "MySQL password" "")"
    [ -n "$DB_PASS" ] || die "MySQL password is required"

    backup "$GRIDCOMMON"
    backup "$OSINI"
    # switch storage provider lines in GridCommon.ini [DatabaseService]
    sed -i -E 's|^\s*;?\s*StorageProvider = "OpenSim.Data.MySQL.dll"|    StorageProvider = "OpenSim.Data.MySQL.dll"|' "$GRIDCOMMON"
    sed -i -E "s|^\s*;?\s*ConnectionString = \"Data Source=[^\"]*\"|    ConnectionString = \"Data Source=${DB_HOST};Database=${DB_NAME};User ID=${DB_USER};Password=${DB_PASS};Old Guids=true;SslMode=None;\"|" "$GRIDCOMMON"
    # drop the SQLite include so MySQL is actually used
    sed -i -E 's|^\s*;?\s*Include-Storage = "config-include/storage/SQLiteStandalone.ini"|    ;Include-Storage = "config-include/storage/SQLiteStandalone.ini"|' "$GRIDCOMMON"
    # re-enable the OfflineIM module (MySQL provider supports it)
    sed -i -E 's|^([[:space:]]*)OfflineMessageModule = .*|\1OfflineMessageModule = "Offline Message Module V2"|' "$OSINI"
    info "GridCommon.ini switched to MySQL (${DB_USER}@${DB_HOST}/${DB_NAME})"
    info "OpenSim creates the tables automatically on first start; offline IM enabled."
else
    backup "$GRIDCOMMON"
    backup "$OSINI"
    # make sure the SQLite include is active
    sed -i -E 's|^\s*;\s*Include-Storage = "config-include/storage/SQLiteStandalone.ini"|    Include-Storage = "config-include/storage/SQLiteStandalone.ini"|' "$GRIDCOMMON"
    # comment out MySQL provider lines if they were enabled by a previous run
    sed -i -E 's|^([[:space:]]*)StorageProvider = "OpenSim.Data.MySQL.dll"|\1;StorageProvider = "OpenSim.Data.MySQL.dll"|' "$GRIDCOMMON"
    sed -i -E 's|^([[:space:]]*)ConnectionString = "Data Source=|\1;ConnectionString = "Data Source=|' "$GRIDCOMMON"
    # disable OfflineIM: SQLite provider has no IOfflineIMData backend and
    # the region would crash on startup otherwise
    sed -i -E 's|^([[:space:]]*)OfflineMessageModule = .*|\1OfflineMessageModule = "" ; disabled: no SQLite OfflineIM backend|' "$OSINI"
    info "SQLite storage active (db files will be created in bin/); offline IM disabled."
    warn "Re-run ./setup.sh and choose MySQL if you need offline messages."
fi

# ---------------------------------------------------------------------------
say "7/7 Firewall and launch"

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then
    if ask_yn "Open port ${PORT}/tcp+udp in ufw? (both are required: TCP=HTTP, UDP=simulator)" "Y" | grep -q yes; then
        ufw allow "${PORT}/tcp" comment 'caprica region' || warn "ufw allow tcp failed - add it manually"
        ufw allow "${PORT}/udp" comment 'caprica region' || warn "ufw allow udp failed - add it manually"
        info "ufw: ${PORT}/tcp and ${PORT}/udp allowed"
    fi
else
    info "ufw not active - make sure inbound TCP ${PORT} (HTTP) and UDP ${PORT} (simulator) are reachable"
fi

cat <<EOF

--------------------------------------------------------------------
Setup complete. Summary:
  Region      : ${REGION_NAME}
  UUID        : ${REGION_UUID}
  Location    : ${LOC_XY}   Size: ${SIZE_X}x${SIZE_Y}
  Port        : ${PORT}/tcp (listener + InternalPort)
  Ext. host   : ${EXTERNAL_HOST}
  BaseScheme  : ${BASE_SCHEME}   (public Caprica services :9000)
  PrivScheme  : ${PRIV_SCHEME}   (private Caprica services :8003)

Next steps:
  1. Make sure /etc/hosts contains a line for your external IP, e.g.:
       ${EXTERNAL_HOST}   localhost
  2. Start the region:
       cd ${BINDIR}
       screen -S ${REGION_NAME,,}
       mono OpenSim.exe
  3. When asked - enter an Estate name, then the FIRST and LAST name
     of your avatar registered on Caprica.
  4. Wait for "Login enabled" in the console.
  5. Add Caprica to your viewer's grid list:
       https://caprica.xyz/services/addcaprica/
  6. In-world: find your region on the map and teleport.

Region management:
  detach screen : Ctrl+A then D
  reattach      : screen -d -r ${REGION_NAME,,}
  logs          : ${BINDIR}/logs/  (and the screen console)
--------------------------------------------------------------------
EOF
