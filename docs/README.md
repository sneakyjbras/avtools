# AV Tools – Documentation

Welcome to the base documentation folder for **AV Tools**. This index points you to the two core areas we document here:

## Index

- **Deployment** — end-to-end operational runbook for packaging, installing, configuring, scheduling, and running AV Tools on AL9.
  → See: [deployment/README.md](deployment/README.md)

- **Database** — data model notes, cache DB schema, and maintenance utilities related to AV Tools.
  → See: [db/README.md](db/README.md)

## Conventions

- Target OS: Alma/Rocky Linux 9
- All services run under the `avdaemon` account
- Secrets are stored in environment files with restricted permissions
- Scheduling uses `systemd` timers (cron entries, if any, should only call the same `systemd` units)

## Managed Configuration

For centrally managed deployments at CERN, consult the Puppet repository:
- https://gitlab.cern.ch/ai/it-puppet-hostgroup-itdcim

## Getting Help

- Check service logs first: `journalctl -u 'avtools@*.service' -b`
- Confirm timers: `systemctl list-timers | grep avtools`
- Validate wrapper usage: `systemctl cat avtools@run-eam.service`

If something’s missing from these docs, please add a section or open a merge request.
