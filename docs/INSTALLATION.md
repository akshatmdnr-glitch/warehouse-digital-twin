# Installation Guide

## Prerequisites

- Ubuntu 24.04
- ROS 2 Jazzy (`ros-jazzy-desktop` or `ros-jazzy-ros-base`)
- Gazebo Harmonic (`ros-jazzy-gazebo-ros-pkgs`)
- Python 3.12
- Optional: Docker + Docker Compose for containerized deployment

## 1. ROS workspace

```bash
sudo apt install python3-colcon-common-extensions python3-pytest

cd ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

## 2. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# run (dev)
export BACKEND_AUTH_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
python -m backend          # http://localhost:8090/docs
```

## 3. Launch everything

```bash
scripts/launch_backend.sh            # backend in the foreground
scripts/launch_all.sh 2              # backend + full ROS twin (2 robots)
./run_demo.sh                        # one-command demo
```

## 4. Verify

```bash
curl http://localhost:8090/api/health   # {"status":"ok",...}
curl http://localhost:8081/api/state    # Control Center state
```

## Headless note

On servers without a display, Gazebo runs headless. Use `xvfb-run`:

```bash
sudo apt install xvfb
scripts/launch_all.sh 2
```

The Control Center, backend and monitoring are pure HTTP services and run
anywhere.
