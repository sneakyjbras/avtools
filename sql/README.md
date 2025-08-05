# SQL Queries for Device Management

This repository contains SQL scripts for managing device data across two database engines: **PostgreSQL** and **InfluxDB**.

## Directory Structure

```
.
├── influx/
│   └── unreachable_devices.sql
└── postgres/
    ├── devices_with_duplicate_serials.sql
    ├── devices_without_serial.sql
    ├── devices_with_serials_in_landb.sql
    └── devices_with_serials_not_in_landb.sql
```

## Scripts

### PostgreSQL

* **devices\_with\_duplicate\_serials.sql** — Find EAM devices sharing the same serial number.
* **devices\_without\_serial.sql** — Identify EAM devices with a NULL or empty serial number.
* **devices\_with\_serials\_in\_landb.sql** — Retrieve EAM devices with non-empty serial numbers that are present in the LANDB database.
* **devices\_with\_serials\_not\_in\_landb.sql** — Retrieve EAM devices with non-empty serial numbers that are NOT present in the LANDB database.

### InfluxDB

* **unreachable\_devices.sql** — Query to identify devices that are unreachable in the InfluxDB data (customize measurement and tag names as needed).

## Conventions

* **Naming pattern:** `<action>_<entity>_<criteria>.sql` (e.g., `find_devices_without_serial.sql`).
* **Engine separation:** Scripts for PostgreSQL live in `postgres/`; scripts for InfluxDB live in `influx/`.
* **Header comments:** Each script should include a brief description, required parameters, and example usage at the top.

## Running Queries in the Console

### PostgreSQL (psql)

Connect:

```bash
psql -h <HOST> -U <USER> -p <PORT> -d <DATABASE>
```

Paste multi-line queries at the prompt and press **Enter** to execute.

Run from a file:

```bash
\i postgres/<script>.sql
```

List available scripts:

```bash
\! ls postgres/
```

### InfluxDB (influx CLI)

Connect:

```bash
influx -host <HOST> -port <PORT> -ssl -unsafeSsl -username <USER> -password '<PASSWORD>' -database <DATABASE>
```

Paste queries at the prompt and press **Enter** to execute.

Run from a file:

```bash
influx query -f influx/<script>.sql
```

## Adding New Scripts

1. Identify the target engine (PostgreSQL or InfluxDB).
2. Follow the naming convention and place the script in the appropriate folder.
3. Add a description and usage examples in the script header.
4. Update this README by adding the new script under the corresponding section.

## License

Specify your project license here (e.g., MIT, Apache 2.0).
