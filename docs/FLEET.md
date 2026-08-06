# Fleet Guide

## Registration & liveness

Each robot's status beacon publishes `/robot_registration` (CSV) and
`/robot_heartbeat` once per second. The Fleet Manager keys its registry by
`robot_id` (re-registrations update, never duplicate). A robot is marked
`OFFLINE` after `heartbeat_timeout` seconds without a heartbeat; a resumed
heartbeat flips it back `ONLINE` (events and alerts fire in both directions).

## Dispatch scoring

For every task the fleet scores eligible robots (ONLINE, idle, not charging,
battery ≥ low threshold, payload capacity ≥ required payload) with a weighted
normalized sum — distance, workload, priority, capability surplus. Lowest
score wins; ties break by `robot_id`. Weights are runtime-tunable
(`score_w_distance`, `score_w_workload`, `score_w_priority`,
`score_w_capability`).

## Reservations & traffic

Each route is rasterized into cells, split into segments
(`segment_size` cells) and reserved as a sliding window
(`traffic_lookahead` segments ahead). Robots release segments behind them as
they advance, so two robots never occupy the same segment. Head-on routes are
serialized at dispatch; blocked robots retry automatically. `cell_size` and
`reservation_buffer` extend the safety margin.

## Battery management

Batteries drain while executing and charge at the station. Below
`low_battery_threshold` a robot gets no new work; at
`critical_battery_threshold` (or when the beacon reports `charging`) its task
is released, re-dispatched and cancelled on the old robot, and it navigates to
its charging station. Charging sessions are recorded in the backend.

## Fault recovery

A robot that fails (heartbeat timeout) has its reservations released, its
pending dispatches returned to the queue, and its unfinished tasks re-dispatched
to the next eligible robot. Recovery events are published on `/recovery_event`
and persisted by the backend.

## Control Center fleet actions

Enable/disable (bridge-level), drain/recharge/set battery (simulation),
restart (beacon reboot), reconnect (forced heartbeat) — all available per robot
in the Fleet tab and via `POST /api/command`.
