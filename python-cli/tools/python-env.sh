#!/usr/bin/env bash
# Sourced by the Linux CLI launchers; project_dir is set by the caller.
user_home="$HOME"
if [[ ${EUID:-$(id -u)} == 0 && -n ${SUDO_USER:-} && $SUDO_USER != root ]]; then
    caller_home=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    if [[ -n "$caller_home" ]]; then
        user_home="$caller_home"
    fi
fi
user_venv="$user_home/.local/share/vcandrive/venv-linux"

if [[ -n ${VCANDRIVE_PYTHON:-} ]]; then
    python_bin="$VCANDRIVE_PYTHON"
elif [[ -x "$project_dir/.venv-linux/bin/python" ]]; then
    python_bin="$project_dir/.venv-linux/bin/python"
else
    python_bin="$user_venv/bin/python"
fi

if [[ ! -x "$python_bin" ]]; then
    echo "error: Python environment not found: $python_bin" >&2
    echo "Create the environment on a local filesystem (HGFS/shared folders may reject symlinks)." >&2
    echo "Run as your normal user, without sudo:" >&2
    printf '  python3 -m venv %q\n' "$user_venv" >&2
    printf '  %q -m pip install -e %q\n' "$user_venv/bin/python" "$project_dir/vkgs_usb" >&2
    echo "For VCAN mode, replace vkgs_usb with vcan_usb." >&2
    echo "For an existing environment, set VCANDRIVE_PYTHON to its absolute Python path." >&2
    exit 127
fi
