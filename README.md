# AV-tools

A simple utility for running authentication-based operations using the CERN LanDB token.

## Requirements

- Ensure you have used CERN LanDB authentication in your current account.
- Define your CERN credentials in your current shell session:
  - Set your username as `MY_USERNAME`
  - Set your password as `MY_PASSWORD`

For example, you can source your credentials file in the terminal:

```bash
source your_credentials_file.sh
```

## Python Poetry Dependencies

This project uses [Poetry](https://python-poetry.org/) for dependency management. You need to install Poetry to run the project.

### Installing Poetry

You can install Poetry by running the following command:

```bash
curl -sSL https://install.python-poetry.org | python3 -
```

After installation, ensure that Poetry is in your PATH.

### Setting up the Environment

Install the project dependencies by running:

```bash
poetry install
```

## Usage

To start the program (if not using Poetry explicitly), simply run:

```bash
./run.sh <token_name>
```

### Quality-Control SQL

All QC scripts live in `sql/`.
Run one manually:
```bash
psql -U avdaemon -h dbod-avtools-cache.cern.ch -p 6613 -d av_cache -c "$(cat sql/devices_with_duplicate_serials.sql)" >> logs/duplicate_serials.log 2>&1
```

Enjoy using AV-Tools!
