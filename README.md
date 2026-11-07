
## Caprica Hypergrid Region

Caprica Hypergrid Opensimulator self-hosted region. Linux

Opensimulator server distribution kit for connecting regions to the Caprica Hypergrid.

See current version in "version.txt".

See "[Instructions.txt](https://github.com/capricagrid/capricaregion/blob/master/Instructions.txt "Instructions.txt")" for region install and setup.

**Windows version**: https://github.com/capricagrid/capricaregionwin


## HTTPS — recommended

Since November 2026 all **public** Caprica grid services are served over **HTTPS**
(`https://g.caprica.xyz:9000`, valid Let's Encrypt certificate). This kit is
pre-configured to use HTTPS for every public service URL: login, gatekeeper,
assets, inventory, profiles, bakes and map tiles.

**Why HTTPS is preferred:**

- Login credentials and session tokens are encrypted in transit — no passive
  sniffing or man-in-the-middle on the way to Caprica.
- Asset and inventory traffic is integrity-protected.
- Plain HTTP on the public port **no longer works** — the Caprica endpoint is
  TLS-only, so HTTPS is effectively required for the public channel.

The protocol is **your choice** and is controlled by two constants in
`bin/OpenSim.ini`, section `[Const]`:

| Constant | Controls | Default | Notes |
|---|---|---|---|
| `BaseScheme` | Public services (`:9000`) | `https` | `http` only works if Caprica re-enables plain HTTP |
| `PrivScheme` | Private services (`:8003`) | `http` | Currently plain HTTP on Caprica's side — do not change unless Caprica support says so |

All service URLs in `bin/config-include/GridCommon.ini` and the literal URLs in
`bin/OpenSim.ini` are built from these constants, so switching the scheme is a
one-line change.

You can also serve **your region's own** client listener over HTTPS — see the
commented example in `bin/OpenSim.ini`, section `[Network]` (requires your own
PKCS#12 certificate valid for your region's `ExternalHostName`). This is
optional: visitors can reach your region over plain HTTP, HTTPS for the region
itself is not required to connect to Caprica.

For a quick automated setup on Linux, run `./setup.sh` — it walks you through
`OpenSim.ini` and `Regions.ini` step by step.


------------


Caprica Hypergrid main website: https://caprica.xyz

Caprica Hypergrid web interface: https://g.caprica.xyz

Support center: https://caprica.xyz/support

Support chat https://caprica.xyz/chat/
