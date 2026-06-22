# MIB Accessibility — research findings

Question: *how many of the fleet's MIBs are behind authentication walls or require
buying the product?* Below is the status for every vendor MIB in scope, after
searching 7 public collections (15,000+ MIBs) and each vendor's distribution
channel.

## Summary

| Vendor MIB | Fleet family | Status | Gate |
|---|---|---|---|
| Server Technology Sentry3 / Sentry4 | AVS PDUs | ✅ **Open** | none — public (LibreNMS/ServerTech). **Have it.** |
| Polycom video | AVV (TC10) | ✅ **Open** | none — public archive. **Have it.** |
| Cisco TelePresence (codec/peripheral) | AVV (Cisco) | ✅ **Open** | none — public (cisco-mibs / GitHub). **Added this round.** |
| **TANDBERG endpoint** (ent. 5596) | AVV (SX20/C-/MXP) | 🟡 **Free but hidden** | redistributable license, *no fee* — but only on robots-blocked snmplink.org + a Cisco forum post. Not in any open collection. |
| **Extron** (ent. 3606) | AVA/AVVS matrices | 🔒 **Auth wall** | requires an **Extron Insider** account (free, but must register as an Extron customer); the MIB itself often requires contacting Extron support. |
| **Radvision / Scopia** (ent. 4063) | AVV (XT5000) | 🔒 **Auth wall** | **Avaya support** download — "only available to customers and partners who are logged in"; manuals say "contact RADVISION and a copy will be provided." |
| **Epson** (ent. 1248) | AVD projectors | 🟡 **Free, bundled** | no licensing fee, but shipped *inside* the free *Epson Projector Management* Windows tool — not published as a standalone file. |

So, of the gated ones:

- **Strictly behind an authentication wall: 2** — **Extron** (Insider account) and
  **Radvision/Scopia** (Avaya partner login).
- **Free but not in any public repository: 2** — **TANDBERG** (robots-blocked
  hosts only) and **Epson** (embedded in free vendor software).
- **None require *buying* anything** — every gate is either a free registration or
  a free download channel; the obstacle is access/redistribution, not purchase.

## The practical shortcut (recommended)

Across the vendor docs, the same pattern recurs: **these devices serve their own
MIB from their web / remote management interface** — e.g. Epson's remote interface
has *SNMP → Download MIB*, and Extron/Radvision SNMP-capable units expose theirs
similarly. Since CERN **owns** these devices on the network, the fastest and most
authoritative way to obtain the Extron, Epson and Radvision MIBs is to pull them
straight off one live unit of each, rather than chasing vendor portals. That also
guarantees the MIB matches the exact firmware in the field.

## What was added to the repo this round

Vendored under `src/avtools/mibs/reference/` (free, public Cisco MIBs; reference
only — these endpoints answer MIB-II, so no handler change yet):

- `CISCO-TELEPRESENCE-MIB.my` — managed objects for Telepresence **codecs and
  peripherals** (display, camera); `ciscoMgmt 643`
- `CISCO-TELEPRESENCE-CALL.mib` — call quality / statistics
- `CISCO-VIDEO-SESSION.mib` — video session stats
- `CISCO-VIDEO-TC.mib` — shared textual conventions for the above

They are placed in `reference/` (not `sources/`) because the codec handler uses
only MIB-II today; if you later want Cisco enterprise OIDs, move the needed module
into `sources/`, add its Cisco dependency MIBs, and run `compile_mibs.py`.

## Still missing (and where to get each)

| MIB | Get it from |
|---|---|
| TANDBERG (5596) | a live SX20/C-series/MXP web UI, or the Cisco Community post "MIBS for TANDBERG MXP and C-SERIES codecs" (kb_67) |
| Extron (3606) | a live DXP/DTP unit's web interface (*SNMP → Download MIB*), or Extron Insider / Extron support |
| Epson (1248) | a live projector's remote interface (*SNMP → Download MIB*), or inside *Epson Projector Management* |
| Radvision (4063) | a live XT5000 web UI, or Avaya support (partner login) |

Send any of these over and I'll vendor + (where it compiles) wire them the same way
as the Sentry MIBs.
