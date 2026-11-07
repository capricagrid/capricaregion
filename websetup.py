#!/usr/bin/env python3
#
# Caprica region WEB installer (Linux)
#
# Browser-based alternative to setup.sh. Single file, Python 3 stdlib only.
#
# Usage (needs root for ufw/screen and for writing into the install dir):
#   sudo python3 websetup.py [--port 8321] [--bind 0.0.0.0] [--once]
#
# On start it prints a one-time URL with a random access token, e.g.:
#   http://1.2.3.4:8321/?key=AbCdEf...
# Open that URL from your browser to run the installer.
#
# --once  shut the web UI down automatically after a successful install
#         (the region itself keeps running).
#
import argparse
import datetime
import html
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer
except ImportError:  # Python < 3.7
    from http.server import HTTPServer
    from socketserver import ThreadingMixIn

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
BINDIR = os.path.join(HERE, "bin")
OSINI = os.path.join(BINDIR, "OpenSim.ini")
GRIDCOMMON = os.path.join(BINDIR, "config-include", "GridCommon.ini")
REGIONS_INI = os.path.join(BINDIR, "Regions", "Regions.ini")
OPENLOG = os.path.join(BINDIR, "OpenSim.log")

TOKEN = secrets.token_urlsafe(18)
ARGS = None
STATE = {"message": "", "error": "", "installed": False}

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
XY_RE = re.compile(r"^[0-9]+,[0-9]+$")
NAME_RE = re.compile(r"^[A-Za-z0-9_ -]+$")


# ---------------------------------------------------------------------------
# helpers (ports of the setup.sh logic)

def backup(path):
    if not os.path.isfile(path):
        return None
    b = "%s.bak-%s" % (path, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    subprocess.run(["cp", "-p", path, b], check=True)
    return b


def set_ini_value(path, key, value):
    """Uncomment-and-set '    KEY = VALUE', or append at EOF (like setup.sh)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    pat = re.compile(r"^\s*;*\s*" + re.escape(key) + r"\s*=")
    done = False
    for i, ln in enumerate(lines):
        if pat.match(ln):
            lines[i] = "    %s = %s\n" % (key, value)
            done = True
    if not done:
        lines.append("\n    %s = %s\n" % (key, value))
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def sub_file(path, pattern, repl, flags=0):
    rx = re.compile(pattern, flags | re.MULTILINE)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    new = rx.sub(repl, text)
    if new != text:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)


def port_free(port):
    try:
        out = subprocess.run(["ss", "-ltn"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return True
    return not re.search(r"[:.]%d\s*$" % port, out, re.M)


def region_name_from_ini():
    try:
        with open(REGIONS_INI, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                m = re.match(r"^\[([^\]]+)\]", ln.strip())
                if m:
                    return m.group(1)
    except OSError:
        pass
    return ""


def session_name():
    n = region_name_from_ini() or "capricaregion"
    n = re.sub(r"[^A-Za-z0-9_-]", "", n.lower()) or "region"
    return n


def screen_running(sess):
    try:
        out = subprocess.run(["screen", "-ls"], capture_output=True, text=True, timeout=10).stdout
        return re.search(r"\.%s\s" % re.escape(sess), out) is not None or \
               re.search(r"\b%s\b" % re.escape(sess), out) is not None
    except Exception:
        return False


def region_running():
    return screen_running(session_name())


def start_region():
    sess = session_name()
    if region_running():
        return True, "already running (screen: %s)" % sess
    if not (os.path.isfile(os.path.join(BINDIR, "OpenSim.exe")) and
            shutil_which("screen") and shutil_which("mono")):
        return False, "OpenSim.exe / screen / mono missing"
    cmd = "cd %s && exec mono OpenSim.exe" % BINDIR
    r = subprocess.run(["screen", "-dmS", sess, "bash", "-c", cmd],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False, "screen failed: %s" % (r.stderr or r.stdout).strip()
    return True, "started in screen session '%s'" % sess


def stop_region():
    sess = session_name()
    if not region_running():
        return True, "not running"
    r = subprocess.run(["screen", "-S", sess, "-X", "quit"],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "").strip() or "stopped"


def shutil_which(cmd):
    from shutil import which
    return which(cmd)


def log_tail(n=120):
    try:
        with open(OPENLOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 60000))
            data = f.read().decode("utf-8", "replace")
        lines = data.splitlines()
        return "\n".join(lines[-n:]) or "(log empty)"
    except OSError as e:
        return "(no log yet: %s)" % e


# ---------------------------------------------------------------------------
# install

def do_install(form):
    """Returns (ok, summary_lines or error)."""
    name = form.get("region_name", "").strip()
    uuid = form.get("region_uuid", "").strip()
    loc = form.get("location", "").strip()
    size = form.get("size", "").strip()
    prims = form.get("max_prims", "").strip()
    port = form.get("port", "").strip()
    host = form.get("external_host", "").strip()
    base_scheme = form.get("base_scheme", "https")
    priv_scheme = form.get("priv_scheme", "http")
    storage = form.get("storage", "sqlite")
    do_ufw = form.get("ufw", "") == "1"

    if not name or not NAME_RE.match(name):
        return False, "Region name is required (letters, digits, space, _ - only)."
    if not UUID_RE.match(uuid):
        return False, "Invalid UUID format."
    if not XY_RE.match(loc):
        return False, "Location must be X,Y integers."
    if not XY_RE.match(size):
        return False, "Size must be X,Y integers."
    if not re.match(r"^\d+$", prims):
        return False, "MaxPrims must be a number."
    if not re.match(r"^\d+$", port) or not (1 <= int(port) <= 65535):
        return False, "Port must be a number 1-65535."
    if not host:
        return False, "External host/IP is required."
    for v in (form.get("db_host", ""), form.get("db_name", ""),
              form.get("db_user", ""), form.get("db_pass", "")):
        if re.search(r'[";\\]', v):
            return False, "MySQL fields may not contain quotes, semicolons or backslashes."

    sx, sy = size.split(",")
    lx, ly = loc.split(",")
    notes = []
    if not port_free(int(port)):
        notes.append("WARNING: something already listens on TCP port %s - "
                     "region may fail to bind" % port)

    # --- Regions.ini ---
    backup(REGIONS_INI)
    regions_txt = """[{name}]
RegionUUID = "{uuid}"
Location = "{loc}"

SizeX = {sx}
SizeY = {sy}

MaxPrims = {prims}

; don't touch this.
InternalAddress = "0.0.0.0"

; Region client port - must match http_listener_port in OpenSim.ini
InternalPort = {port}


AllowAlternatePorts = False

; External address visitors use to reach this region.
ExternalHostName = "{host}"
""".format(name=name, uuid=uuid, loc=loc, sx=sx, sy=sy, prims=prims,
           port=port, host=host)
    with open(REGIONS_INI, "w", encoding="utf-8") as f:
        f.write(regions_txt)
    notes.append("Regions.ini written")

    # --- OpenSim.ini ---
    backup(OSINI)
    set_ini_value(OSINI, "http_listener_port", port)
    set_ini_value(OSINI, "BaseScheme", '"%s"' % base_scheme)
    set_ini_value(OSINI, "PrivScheme", '"%s"' % priv_scheme)
    notes.append("OpenSim.ini patched (port %s, BaseScheme=%s, PrivScheme=%s)"
                 % (port, base_scheme, priv_scheme))

    # --- storage ---
    backup(GRIDCOMMON)
    backup(OSINI)
    if storage == "mysql":
        db_host = form.get("db_host", "127.0.0.1").strip() or "127.0.0.1"
        db_name = form.get("db_name", "opensim").strip() or "opensim"
        db_user = form.get("db_user", "opensim").strip() or "opensim"
        db_pass = form.get("db_pass", "")
        if not db_pass:
            return False, "MySQL password is required."
        conn = ('Data Source=%s;Database=%s;User ID=%s;Password=%s;'
                'Old Guids=true;SslMode=None;' % (db_host, db_name, db_user, db_pass))
        sub_file(GRIDCOMMON,
                 r'^\s*;?\s*StorageProvider = "OpenSim\.Data\.MySQL\.dll"',
                 '    StorageProvider = "OpenSim.Data.MySQL.dll"')
        sub_file(GRIDCOMMON,
                 r'^\s*;?\s*ConnectionString = "Data Source=[^"]*"',
                 '    ConnectionString = "%s"' % conn)
        sub_file(GRIDCOMMON,
                 r'^\s*;?\s*Include-Storage = "config-include/storage/SQLiteStandalone\.ini"',
                 '    ;Include-Storage = "config-include/storage/SQLiteStandalone.ini"')
        sub_file(OSINI,
                 r'^([ \t]*)OfflineMessageModule = .*',
                 r'\1OfflineMessageModule = "Offline Message Module V2"')
        notes.append("MySQL configured (%s@%s/%s); offline IM enabled" % (db_user, db_host, db_name))
    else:
        sub_file(GRIDCOMMON,
                 r'^\s*;\s*Include-Storage = "config-include/storage/SQLiteStandalone\.ini"',
                 '    Include-Storage = "config-include/storage/SQLiteStandalone.ini"')
        sub_file(GRIDCOMMON,
                 r'^([ \t]*)StorageProvider = "OpenSim\.Data\.MySQL\.dll"',
                 r'\1;StorageProvider = "OpenSim.Data.MySQL.dll"')
        sub_file(GRIDCOMMON,
                 r'^([ \t]*)ConnectionString = "Data Source=',
                 r'\1;ConnectionString = "Data Source=')
        sub_file(OSINI,
                 r'^([ \t]*)OfflineMessageModule = .*',
                 r'\1OfflineMessageModule = "" ; disabled: no SQLite OfflineIM backend')
        notes.append("SQLite storage; offline IM disabled")

    # --- firewall ---
    if do_ufw and shutil_which("ufw"):
        try:
            st = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=15).stdout
        except Exception:
            st = ""
        if "Status: active" in st:
            for proto in ("tcp", "udp"):
                r = subprocess.run(["ufw", "allow", "%s/%s" % (port, proto),
                                    "comment", "caprica region"],
                                   capture_output=True, text=True)
                notes.append("ufw %s/%s: %s" % (port, proto,
                             "ok" if r.returncode == 0 else "FAILED"))
        else:
            notes.append("ufw not active - open TCP+UDP %s manually" % port)
    else:
        notes.append("ufw step skipped - ensure TCP+UDP %s reachable" % port)

    # --- autostart ---
    ok, msg = start_region()
    notes.append("region: %s" % msg)

    summary = [
        "Region: %s  UUID: %s" % (name, uuid),
        "Location: %s  Size: %sx%s  MaxPrims: %s" % (loc, sx, sy, prims),
        "Port: %s  External: %s" % (port, host),
        "BaseScheme: %s  PrivScheme: %s" % (base_scheme, priv_scheme),
    ] + notes
    return True, summary


# ---------------------------------------------------------------------------
# HTML

CSS = """
body{font-family:system-ui,Arial,sans-serif;background:#1e1e28;color:#e8e8f0;
     max-width:860px;margin:24px auto;padding:0 16px}
h1{font-size:1.4em} h2{font-size:1.1em;margin-top:28px}
label{display:block;margin:10px 0 3px;font-size:.9em;color:#aab}
input,select{width:100%;padding:7px;border-radius:6px;border:1px solid #444;
     background:#12121a;color:#eee;box-sizing:border-box}
input[type=checkbox]{width:auto}
button,.btn{display:inline-block;padding:9px 18px;border:0;border-radius:6px;
     background:#4a7cff;color:#fff;cursor:pointer;text-decoration:none;font-size:.95em}
button.secondary{background:#444}.btn{background:#2c5cb8}
.warn{background:#3a2c14;border:1px solid #8a6d1f;padding:10px;border-radius:8px}
.ok{background:#14301c;border:1px solid #2e7d4f;padding:10px;border-radius:8px}
.err{background:#3a1414;border:1px solid #a33;padding:10px;border-radius:8px}
pre{background:#12121a;border:1px solid #333;border-radius:8px;padding:12px;
     overflow:auto;max-height:340px;font-size:.8em;white-space:pre-wrap}
table.kv td{padding:4px 12px 4px 0;color:#aab}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}
@media(max-width:640px){.grid{grid-template-columns:1fr}}
"""


def page(title, body):
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>%s</title><style>%s</style></head><body>%s</body></html>"
            % (html.escape(title), CSS, body))


def keyok(qs):
    return qs.get("key", [""])[0] == TOKEN


def authed_form_fields(extra=""):
    return "<input type='hidden' name='key' value='%s'>%s" % (TOKEN, extra)


def install_form():
    f = """
<h1>Caprica region installer</h1>
<p class='warn'>This is a one-time setup tool. Keep this URL private - anyone
with the key can reconfigure and restart the region.</p>
<form method='post' action='/install'>%s
<div class='grid'>
<label>Region name (map display name)</label>
<input name='region_name' required placeholder='MyRegion'>
<label>Region UUID <a href='https://gridlist.ru/uuid/'>generator</a></label>
<input name='region_uuid' required placeholder='xxxxxxxx-xxxx-...'>
<label>Location X,Y <a href='https://caprica.xyz/simple-map/'>pick on map</a></label>
<input name='location' value='1000,1000'>
<label>Size X,Y (256 / 512 / 1024)</label>
<input name='size' value='256,256'>
<label>MaxPrims</label>
<input name='max_prims' value='25000'>
<label>Region port (TCP+UDP must be reachable)</label>
<input name='port' value='9022'>
<label>External host name or IP (as visitors reach it)</label>
<input name='external_host' required placeholder='203.0.113.10 or region.example.com'>
</div>
<h2>Caprica services</h2>
<label>Public services (port 9000)</label>
<select name='base_scheme'><option value='https'>https (recommended - TLS only on Caprica side)</option>
<option value='http'>http (only if Caprica re-enables it)</option></select>
<label>Private services (port 8003)</label>
<select name='priv_scheme'><option value='http'>http (current Caprica setting)</option>
<option value='https'>https (only if Caprica support confirms)</option></select>
<h2>Database</h2>
<label>Storage</label>
<select name='storage'><option value='sqlite'>SQLite (default - no setup, offline IM disabled)</option>
<option value='mysql'>MySQL (required for offline IM)</option></select>
<div id='mysql' style='display:none'>
<div class='grid'>
<label>MySQL host</label><input name='db_host' value='127.0.0.1'>
<label>Database name</label><input name='db_name' value='opensim'>
<label>MySQL user</label><input name='db_user' value='opensim'>
<label>MySQL password</label><input name='db_pass' type='password'>
</div></div>
<h2>Firewall</h2>
<label><input type='checkbox' name='ufw' value='1' checked>
Open PORT/tcp and PORT/udp in ufw (both required!)</label>
<p><button type='submit'>Install &amp; start region</button></p>
</form>
<script>document.querySelector('[name=storage]').onchange=function(){
document.getElementById('mysql').style.display=this.value=='mysql'?'block':'none'};</script>
""" % authed_form_fields()
    return page("Caprica region installer", f)


def status_page():
    name = region_name_from_ini() or "(not configured)"
    running = region_running()
    state = "RUNNING (screen: %s)" % session_name() if running else "STOPPED"
    flash = ""
    if STATE.get("message"):
        flash += "<div class='ok'><pre>%s</pre></div>" % html.escape(STATE["message"])
        STATE["message"] = ""
    if STATE.get("error"):
        flash += "<div class='err'>%s</div>" % html.escape(STATE["error"])
        STATE["error"] = ""
    body = flash + """
<h1>Caprica region</h1>
<table class='kv'>
<tr><td>Region</td><td><b>%s</b></td></tr>
<tr><td>Status</td><td>%s</td></tr>
</table>
<p>
<a class='btn' href='/log?key=%s'>View log</a>
<form method='post' action='/start' style='display:inline'>%s<button>Start</button></form>
<form method='post' action='/stop' style='display:inline'>%s<button class='secondary'>Stop</button></form>
<a class='btn' href='/install?key=%s' style='background:#555'>Reconfigure</a>
<form method='post' action='/shutdown' style='display:inline'>%s<button class='secondary'>Shut down installer</button></form>
</p>
<h2>Recent log</h2>
<pre>%s</pre>
<p class='warn'>First start: OpenSim asks for an Estate name and your avatar's
first/last name <b>in the screen console</b>. Attach once with
<code>screen -r %s</code>, answer the prompts, then detach (Ctrl+A, D).
Look for "Login enabled" in the log.</p>
""" % (html.escape(name), state, TOKEN,
       authed_form_fields(), authed_form_fields(), html.escape(TOKEN),
       authed_form_fields(), html.escape(log_tail(80)), html.escape(session_name()))
    return page("Caprica region", body)


def log_page(n=200):
    try:
        n = max(10, min(1000, int(n)))
    except Exception:
        n = 200
    body = ("<h1>OpenSim.log (last %d lines)</h1><p><a href='/'>back</a></p>"
            "<pre>%s</pre>" % (n, html.escape(log_tail(n))))
    return page("Region log", body)


# ---------------------------------------------------------------------------
# HTTP handler

class Handler(BaseHTTPRequestHandler):
    server_version = "CapricaRegionSetup/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[websetup] %s\n" % (fmt % args))

    def _send(self, code, text, ctype="text/html; charset=utf-8"):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _qs(self):
        return parse_qs(urlparse(self.path).query)

    def do_GET(self):
        u = urlparse(self.path)
        qs = self._qs()
        if not keyok(qs):
            return self._send(403, page("Forbidden", "<h1>403</h1><p>Bad or missing access key.</p>"))
        if u.path == "/":
            if region_name_from_ini():
                return self._send(200, status_page())
            return self._send(200, install_form())
        if u.path == "/install":
            return self._send(200, install_form())
        if u.path == "/log":
            return self._send(200, log_page(qs.get("n", ["200"])[0]))
        return self._send(404, page("Not found", "<h1>404</h1>"))

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        form = {k: v[0] for k, v in parse_qs(raw).items()}
        if form.get("key") != TOKEN:
            return self._send(403, page("Forbidden", "<h1>403</h1><p>Bad or missing access key.</p>"))

        if u.path == "/install":
            try:
                ok, res = do_install(form)
            except Exception as e:
                ok, res = False, "Install failed: %s" % e
            STATE["installed"] = ok
            if ok:
                STATE["message"] = "\n".join(res if isinstance(res, list) else [str(res)])
                STATE["error"] = ""
                if ARGS is not None and ARGS.once:
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                STATE["error"] = str(res)
            self._redirect("/?key=%s" % TOKEN)
            return
        if u.path == "/start":
            ok, msg = start_region()
            STATE["error" if not ok else "message"] = msg
        elif u.path == "/stop":
            ok, msg = stop_region()
            STATE["error" if not ok else "message"] = msg
        elif u.path == "/shutdown":
            self._send(200, page("Bye", "<h1>Installer stopped.</h1>"
                       "<p>The region keeps running if it was started. "
                       "Restart the installer any time with "
                       "<code>sudo python3 websetup.py</code></p>"))
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self._redirect("/?key=%s" % TOKEN)


def main():
    global ARGS
    ap = argparse.ArgumentParser(description="Caprica region web installer")
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--once", action="store_true",
                    help="exit automatically after a successful install")
    ARGS = ap.parse_args()

    if not os.path.isfile(OSINI):
        sys.exit("OpenSim.ini not found at %s - run from the repo root" % OSINI)
    for cmd in ("python3",):
        pass
    for cmd in ("mono", "screen"):
        from shutil import which
        if not which(cmd):
            print("warning: '%s' not found in PATH" % cmd, file=sys.stderr)

    try:
        httpd = ThreadingHTTPServer((ARGS.bind, ARGS.port), Handler)
    except OSError as e:
        sys.exit("Cannot bind %s:%d - %s. Pick another port: "
                 "--port 8080 (or any free port; avoid 80/443 if Apache/nginx runs)."
                 % (ARGS.bind, ARGS.port, e))
    # guess the public URL for the banner
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = ARGS.bind
    print("=" * 60)
    print(" Caprica region web installer")
    print(" Listening on %s:%d" % (ARGS.bind, ARGS.port))
    print(" Open this URL in your browser:")
    print("   http://%s:%d/?key=%s" % (ip, ARGS.port, TOKEN))
    print(" The key is required for every action. Do not share this URL.")
    if ARGS.once:
        print(" --once: this installer will exit after a successful install.")
    print(" Press Ctrl+C to stop the installer (region keeps running).")
    print("=" * 60)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
