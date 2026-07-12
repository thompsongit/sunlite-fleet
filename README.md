# Sunlite Scheduler

Sunlite Scheduler controls a configurable fleet of ABET Sunlite Model 11002 solar simulators from one Raspberry Pi.

Each simulator has an independent schedule, commanded state, history, and relay-channel mapping. Use **Stop All** to command every configured simulator OFF, or select a device such as **Sunlite 11002 - A** for individual controls.

Schedules support:

- regular ON/OFF cycles with a repeat count or end time;
- one-off custom transition timelines;
- independent concurrent operation on different simulators;
- interruption recovery configured per schedule.

The interface reports commanded relay state. It does not confirm the simulator's electrical or optical output unless hardware feedback is added.
