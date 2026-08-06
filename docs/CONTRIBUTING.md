# Contributing

We welcome contributions. Please follow the repository's conventions.

## Process

1. Fork and create a feature branch.
2. Make changes, add tests, keep the diff focused.
3. Run the quality gate locally (see below).
4. Open a pull request — CI runs lint, backend tests, ROS build/tests and
   Docker builds.

## Quality gate

```bash
black --check backend/ ros2_ws/src/warehouse_bringup/warehouse_bringup/
isort --check backend/ ros2_ws/src/warehouse_bringup/warehouse_bringup/
flake8 backend/backend/ ros2_ws/src/warehouse_bringup/warehouse_bringup/
pylint --rcfile=pylintrc backend/backend/
mypy --config-file pyproject.toml backend/backend/
cd backend && python -m pytest tests/
cd ros2_ws && python -m pytest src/warehouse_bringup/test/
```

## Conventions

- ROS nodes observe existing topics; only the Control Center publishes
  operator commands.
- Backend has no `rclpy`; use the ingest bridge.
- CSV on ROS, JSON on the backend; append new fields, never reorder.
- Format with `black`, sort imports with `isort`.
- Keep docstrings for every public function.

## Code of conduct

See [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
