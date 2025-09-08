# Quick Commands Reference

A succinct list of database connection commands, with usernames and passwords hidden for security.

---

## Environment Variables

For convenience and security, set the following environment variables in your shell or your `~/.env` file:

```bash
# PostgreSQL credentials
export PG_HOST="dbod-avtools-cache.cern.ch"
export PG_PORT="6613"
export PG_USER="<your_username>"
export PG_DATABASE="cache_db"

# InfluxDB credentials
export INFLUX_HOST="dbod-avtools-ts.cern.ch"
export INFLUX_PORT="8090"
export INFLUX_USER="<your_username>"
export INFLUX_PASSWORD="<your_password>"
export INFLUX_DB="av_ts"
```

---

## PostgreSQL

Connect to our PostgreSQL cache database:

```bash
psql \
  -h "$PG_HOST" \
  -p "$PG_PORT" \
  -U "$PG_USER" \
  "$PG_DATABASE"
```

> **Tip:** With `~/.pgpass` configured, you won’t be prompted for a password.

## InfluxDB

Connect to our InfluxDB time-series database:

```bash
influx \
  -host "$INFLUX_HOST" \
  -port "$INFLUX_PORT" \
  -ssl \
  -unsafeSsl \
  -username "$INFLUX_USER" \
  -password "$INFLUX_PASSWORD" \
  -database "$INFLUX_DB"
```

> **Note:** `-unsafeSsl` allows self-signed certificates. Remove if using a CA-signed certificate.

## Tips

* **Shell aliases** can speed up access without exposing credentials in history:

  ```bash
  alias pgcache="psql -h $PG_HOST -p $PG_PORT -U $PG_USER $PG_DATABASE"
  alias influxe="influx -host $INFLUX_HOST -port $INFLUX_PORT -ssl -unsafeSsl -username $INFLUX_USER -password '$INFLUX_PASSWORD' -database $INFLUX_DB"
  ```

* **Secure storage:**

  * For PostgreSQL, keep credentials in `~/.pgpass` with `chmod 600` permissions.
  * For other CLI tools, use environment variables or credential files rather than typing passwords interactively.
