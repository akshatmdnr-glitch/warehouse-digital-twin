# FAQ

**Q: Do the dashboard and backend change robot behavior?**
No. Every observer is a read-only subscriber. Operator actions publish only to
existing control topics (`/cmd_vel`, `/goal_pose`, `/add_task`, `/cancel_task`,
`/robot_heartbeat`) plus two simulation-only beacon topics
(`/battery_command`, `/control`).

**Q: Why SQLite instead of PostgreSQL?**
SQLite is the documented fallback, zero-dependency, transactional and persists
in one file. The repository layer isolates all SQL, so a PostgreSQL backend can
be swapped by replacing `backend/database.py`.

**Q: How does state survive a restart?**
The backend persists robots, tasks, fleet status, reservations, events, alerts,
analytics, positions, battery history and charging sessions. On restart it
reads the same database and re-derives nothing; the ROS side re-registers on
the first heartbeat.

**Q: Can I run 10+ robots?**
Yes — `robot_count` and per-robot launch includes scale linearly. The fleet
manager, analytics and backend are all stateless w.r.t. robot count. See
`scripts/perf_benchmark.py` for 50/100/250-robot ingest measurements.

**Q: How do I add authentication to the Control Center itself?**
The backend enforces authentication on all data endpoints. The Control Center
can be placed behind the same identity by configuring the backend URL (its
proxy authenticates server-side).

**Q: What is a "read-only dashboard mode"?**
A token issued with `readonly: true` can read everything but every mutating
endpoint returns 403.

**Q: Why does Gazebo need a display?**
The rendering engine requires a GL context. Use `xvfb-run -a` headless; the
rest of the platform is pure HTTP/ROS and runs anywhere.
