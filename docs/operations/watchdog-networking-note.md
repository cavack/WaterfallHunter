# Watchdog alerting-network routing

The watchdog and Alertmanager share the Compose `alerting` network.

Do not assign the watchdog a fixed container IP. Docker IPAM owns address allocation on this network, and a fixed address can collide with an already attached container.

Alertmanager reaches the watchdog through Docker DNS using the service/container name:

`http://waterfall-watchdog:8080/alerts`

This removes the dependency on `172.23.0.2` while preserving the internal-only alerting network. Production remains signal-only; this change does not enable trading or order placement.
