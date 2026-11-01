# Future work

These are unverified ideas retained from older implementation and presence plans,
not commitments or a list of missing deployed features. Check the code and live
configuration before starting any of them. Physical tasks for Nick belong only
in [REMINDERS.md](REMINDERS.md).

- Explore Glow/MQTT as an independent meter source and compare against the ESP32.
- Review Graphite retention, backups and optional periodic exports.
- Consider cost panels, presence overlays, arrival/departure annotations and alerts
  where existing Grafana dashboards do not already cover them.
- Validate presence transitions, source outages, grace periods and phone MAC changes;
  consider guest counting and further failure isolation if observations justify it.
- Revisit [adaptive Tuya quota management](TUYA_CLOUD_QUOTA.md) only if cloud polling
  is needed again. The blackpi2 cloud service was retired on 2026-11-01.
- Discovery ideas: rename UI, new-device alerts, grouping and identity history.

Kasa/Tuya collectors, the ESP32 meter, oven monitoring and dashboards already have
operational documentation. Their original build-from-scratch phases and timeline
estimates are retained in Git history rather than presented as next steps.
