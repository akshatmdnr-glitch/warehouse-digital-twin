#!/usr/bin/env bash
# Launch a GUI command with a clean environment.
#
# When the shell runs inside the VS Code (snap) app, environment variables
# such as GTK_PATH, GIO_MODULE_DIR, LOCPATH and XDG_DATA_DIRS point into
# /snap/code/... so the dynamic linker resolves /snap/core20/.../libpthread
# which crashes Qt/OGRE GUIs (Gazebo GUI, RViz2) with:
#   undefined symbol: __libc_pthread_init, version GLIBC_PRIVATE
#
# This wrapper removes those snap variables (restoring the host defaults) and
# runs the given command with everything else (ROS env, DISPLAY) intact.
#
# Usage:
#   source /opt/ros/jazzy/setup.bash
#   source ros2_ws/install/setup.bash
#   scripts/launch_gui.sh rviz2
#   scripts/launch_gui.sh ros2 launch warehouse_bringup warehouse.launch.py ...
#
set -e

# Force FastDDS onto a single UDP transport (no shared-memory) to avoid ghost
# discovery participants left behind by the many short-lived sim runs.
export FASTRTPS_DEFAULT_PROFILES_FILE="$(dirname "$0")/fastdds_no_shm.xml"
export ROS_LOCALHOST_ONLY=1

exec env -u GTK_PATH -u GIO_MODULE_DIR -u GTK_IM_MODULE_FILE -u LOCPATH \
    -u XDG_DATA_DIRS -u XDG_DATA_HOME -u GSETTINGS_SCHEMA_DIR -u GTK_EXE_PREFIX \
    -u SNAP -u SNAP_NAME -u SNAP_COMMON -u SNAP_USER_DATA -u SNAP_INSTANCE_NAME \
    -u SNAP_REVISION -u SNAP_VERSION -u SNAP_ARCH -u SNAP_LAUNCHER \
    "$@"
