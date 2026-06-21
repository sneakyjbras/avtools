# av-tools — MIB Manifest (fleet ↔ MIB ↔ source)

Mapped 1:1 to the distinct manufacturer/model in `snmp_able_devices.csv` (SNMP=ON).
Bundle holds every MIB I could retrieve from a public, verifiable source; the rest
are listed with exact origins (they sit behind vendor portals or robots-blocked
hosts and are not in any open MIB collection).

Searched for the niche AV MIBs across 7 public collections — **>15,000 MIB files**
(netdisco-mibs, librenms-mibs, deworn, hsnodgrass, kcsinclair, Ferroin, 4rCh3r).

---

## Retrieved (in this bundle — verified genuine)

| File | Covers (fleet) | Enterprise / scope | Source | Verified |
|---|---|---|---|---|
| `servertech/Sentry4-MIB` | **AVS PDUs** `CW-16HEK452/CW-16HEU452CR/CW-8HEA211` (PRO2) | `1.3.6.1.4.1.1718.4` | LibreNMS `mibs/sentry` | ✅ ServerTech © header; 4817 lines |
| `servertech/Sentry3-MIB` | **AVS PDUs** `AA12J/AA12G/AA13A/MFG-AA11K` (CDU) | `1.3.6.1.4.1.1718.3` | LibreNMS `mibs/sentry` | ✅ 2365 lines |
| `polycom/POLYCOM.mib` | **AVV** Polycom `TC10` | Polycom Video MIB | snmp_mib_archive | ✅ "Polycom Video MIB"; 414 lines |
| `standard/SNMPv2-MIB` | **all** (sysDescr/sysUpTime) — what codecs & matrices actually answer | MIB-II `1.3.6.1.2.1.1` | LibreNMS | ✅ |
| `standard/RFC1213-MIB` | **all** (MIB-II) | `1.3.6.1.2.1` | LibreNMS | ✅ |
| `standard/IF-MIB` | **all** (interfaces) | `1.3.6.1.2.1.2/31` | LibreNMS | ✅ |

The MIB-II set is the operationally important one for the codec/matrix families —
Phase 1 established those devices expose **only** MIB-II over SNMP, which is fully
covered here. Server Technology is the only family with rich enterprise telemetry,
and **both** its MIBs are present and verified.

---

## Not in any public collection — get from vendor (reference-only for us)

These four are reference-only: the devices answer essentially just MIB-II (already
in the bundle), so the enterprise MIBs don't change the exporter. None appear in any
of the 7 open collections searched.

| MIB | Covers (fleet) | Enterprise | Where it actually lives |
|---|---|---|---|
| **TANDBERG.mib** | AVV Cisco/Tandberg `SX20, C20, C40, CS-CODEC-PLUS, CS-KIT-*, MXP 3000, Webex Room Kit` | `1.3.6.1.4.1.5596` (`tms = tandberg 110`) | snmplink.org `…/T/Tandberg/Tandberg ASA/videoconf/TANDBERG.mib` (robots-blocked to automation); also pasted in full on Cisco Community thread "MIBS for TANDBERG MXP and C-SERIES codecs" |
| **Extron MIB(s)** | AVA/AVVS `DXP 44/84/88 HD 4K, DXP 1616 HD, DTP CrossPoint 84` | `1.3.6.1.4.1.3606` | Extron Download Center → extron.com/download (search "SNMP"); JS-gated portal |
| **Epson projector MIB** | AVD `EB-L530U/L510U/2250U/4950WU/L720U…` | `1.3.6.1.4.1.1248` | Bundled with *Epson Projector Management* / *EasyMP Monitor* (Windows); proprietary, not redistributed |
| **Radvision/Scopia MIB** | AVV `SCOPIA XT 5000` | `1.3.6.1.4.1.4063` | Avaya/Radvision support portal |

> Note: the **Epson** enterprise MIB is the one with real value (the existing
> `EpsonProjector` handler reads `1248` OIDs). It ships only inside Epson's Windows
> management tool. If you can extract it from an install, send it over and I'll fold
> it in + verify the projector OIDs the same way I did for the PDUs below.

---

## Bonus: Phase 2 PDU handler verified against the real Sentry MIBs

Having the authoritative MIBs let me confirm every OID and scale factor in the PDU
handlers — including the **two items I'd flagged in Phase 2 as "confirm on live
hardware." Both are now confirmed correct from the MIB:**

| Handler assumption | MIB says | Verdict |
|---|---|---|
| Sentry4 `st4LineCurrent` ÷100 | `UNITS "hundredth Amps"` | ✅ |
| Sentry4 `st4LineCurrentUtilized` ÷10 | `UNITS "tenth percent"` | ✅ |
| Sentry4 `st4PhaseVoltage` ÷10 | `UNITS "tenth Volts"` | ✅ |
| Sentry4 active power = W | `UNITS "Watts"` | ✅ |
| Sentry4 `DeviceStatus` nominal = 0 | `normal(0)` | ✅ |
| Sentry4 `st4InputCordStatus` col 2 → `…1718.4.1.3.3.1.2.x.x` | `::= { st4InputCordMonitorEntry 2 }` | ✅ |
| **Sentry3 `infeedLoadValue` ÷100** *(was flagged)* | `UNITS "hundredth Amps"` / "measured load in hundredths of Amps" | ✅ confirmed |
| **Sentry3 `systemVersion` = `…1718.3.1.1.0`** *(was flagged)* | `systemVersion ::= { systemGroup 1 }`, `systemGroup ::= { sentry3 1 }` | ✅ confirmed |
| Sentry3 `infeedStatus` nominal = on(1) | `off(0), on(1), …` | ✅ |

Net: the PDU implementation needs **no changes** — the live-hardware caveats from the
Phase 2 notes can be closed.

---

## How each MIB was sourced (reproducible)

```
# Server Technology + IETF (LibreNMS canonical mirror)
curl -O https://raw.githubusercontent.com/librenms/librenms/master/mibs/sentry/Sentry4-MIB
curl -O https://raw.githubusercontent.com/librenms/librenms/master/mibs/sentry/Sentry3-MIB
curl -O https://raw.githubusercontent.com/librenms/librenms/master/mibs/SNMPv2-MIB
curl -O https://raw.githubusercontent.com/librenms/librenms/master/mibs/IF-MIB
curl -O https://raw.githubusercontent.com/librenms/librenms/master/mibs/RFC1213-MIB
# Polycom: from the snmp_mib_archive (deworn/hsnodgrass), file p/polycom.mib
```

Runtime note: av-tools does **not** load MIB files — handlers use numeric OIDs
directly. These are for reference, OID verification, and future enterprise-OID work
(e.g. Epson, or Extron once its MIB is in hand).

---

## Reference MIBs (added — free/public, not compiled)

Under `reference/` — vendored for documentation and future enterprise-OID work.
The codec/matrix families answer only MIB-II today, so these are not compiled into
metadata (that would pull a large Cisco dependency tree for no current runtime use).

| File | Scope | Source |
|---|---|---|
| `CISCO-TELEPRESENCE-MIB.my` | Telepresence codecs + peripherals (display/camera), `ciscoMgmt 643` | cisco/cisco-mibs (GitHub) |
| `CISCO-TELEPRESENCE-CALL.mib` | call quality / statistics | Cisco public MIBs |
| `CISCO-VIDEO-SESSION.mib` | video session statistics | Cisco public MIBs |
| `CISCO-VIDEO-TC.mib` | shared textual conventions | Cisco public MIBs |

See `ACCESSIBILITY.md` for the full auth-wall / availability analysis of every
fleet vendor MIB and how to obtain the ones still missing.
