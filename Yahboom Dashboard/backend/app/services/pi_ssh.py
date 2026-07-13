"""Shared SSH helpers for Raspberry Pi remote commands."""

from __future__ import annotations

import paramiko

from app.services.mqtt_service import mqtt_service
from config import (
    DEFAULT_BROKER_IP,
    PI_SSH_KEY_PATH,
    PI_SSH_PASSWORD,
    PI_SSH_USER,
)


def resolved_host() -> str:
    return mqtt_service.broker_ip or DEFAULT_BROKER_IP


def pi_home() -> str:
    return "/root" if PI_SSH_USER == "root" else f"/home/{PI_SSH_USER}"


def expand_pi_path(path: str, home: str | None = None) -> str:
    home = home or pi_home()
    if path.startswith("~/"):
        return f"{home}/{path[2:]}"
    if path.startswith("~"):
        return path.replace("~", home, 1)
    return path


def ssh_client(host: str | None = None) -> paramiko.SSHClient:
    host = host or resolved_host()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs: dict = {"username": PI_SSH_USER, "timeout": 10}
    if PI_SSH_KEY_PATH:
        connect_kwargs["key_filename"] = PI_SSH_KEY_PATH
    else:
        connect_kwargs["password"] = PI_SSH_PASSWORD
    client.connect(host, **connect_kwargs)
    return client
