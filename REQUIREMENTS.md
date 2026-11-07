# Requirements — self-hosted Caprica region

Everything needed to run your own region connected to the Caprica grid.
Two installers are provided: `websetup.py` (browser UI, recommended) and
`setup.sh` (console wizard). Both do the same configuration work.

## Software

| Component | Why | Install (Debian/Ubuntu) |
|---|---|---|
| `mono-complete` | Runs OpenSim.exe | `sudo apt install mono-complete` |
| `screen` (or `tmux`) | Keeps the region alive after logout | `sudo apt install screen` |
| `git` | Clone this repository | `sudo apt install git` |
| `python3` | Web installer (`websetup.py`) | preinstalled on Ubuntu; `sudo apt install python3` |
| `ufw` (optional) | Firewall rules for the region port | `sudo apt install ufw` |
| `mysql-server` (optional) | Required **only** for offline IM | `sudo apt install mysql-server` |

Windows users: see the companion repository `capricaregionwin` — it ships a
ready-made Windows build (no Mono needed) and the same configuration steps.

## Hardware

A region is lightweight. Minimum practical specs:

- **CPU:** 1 vCPU is enough for a 256×256 region; 2 vCPU comfortable.
- **RAM:** ~512 MB for the region process; 1 GB recommended.
- **Disk:** ~1 GB free (region kit ~400 MB + SQLite/MySQL data + asset cache).
- A 512×1024 or 1024×1024 region benefits from more RAM/CPU but the same
  software stack works.

## Network

### Inbound (visitors → your region)

- **TCP + UDP** on your region port (default `9022`, configurable).
  Both are required: TCP serves HTTP/capabilities, UDP is the simulator
  protocol. Opening only TCP lets the region register but teleports hang
  and time out.
- UDP must not be blocked by NAT without port forwarding — OpenSim uses
  UDP for the viewer handshake.

### Outbound (your region → Caprica grid)

- `g.caprica.xyz:9000` — **HTTPS only** (public services: gatekeeper,
  assets, grid services). Plain HTTP no longer works there.
- `g.caprica.xyz:8003` — plain **HTTP** (private services: inventory,
  authentication, presence, estate data).
- Outbound TCP 443/80 for OS updates and asset fetches.

### Addressing

- `ExternalHostName` in `Regions.ini` may be a DNS name **or a bare IP**.
  With an IP-only setup the region's own listener stays plain HTTP
  (trusted certificates for bare IPs are impractical).
- If you use an IP, add it to `/etc/hosts` pointing at `localhost` so the
  region can reach itself (loopback):
  `127.0.0.1 localhost` and `<your external IP> localhost`

## Database

- **SQLite (default):** zero setup — `*.db` files are created inside
  `bin/`. Limitation: the bundled `OpenSim.Data.SQLite.dll` has no OfflineIM
  backend, so offline messages are disabled automatically.
- **MySQL (optional):** required for offline IM. Install `mysql-server`,
  create a database + user; OpenSim creates tables on first start.

## First-run notes

- On first start OpenSim asks for an **Estate name** and your avatar's
  **first/last name** (registered on Caprica) in the screen console —
  attach once with `screen -r <region>` to answer.
- Region must appear on `https://caprica.xyz/simple-map/` before you pick
  free coordinates for `Location` in `Regions.ini`.

## Quick start

```bash
git clone https://github.com/capricagrid/capricaregion.git /opt/capricaregion
cd /opt/capricaregion
sudo python3 websetup.py --port 8080   # or: ./setup.sh
# open the printed URL in your browser
```
