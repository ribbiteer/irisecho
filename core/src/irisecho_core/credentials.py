# SPDX-License-Identifier: AGPL-3.0-or-later
"""Access tokens for model hosts, kept in the operating system's keychain.

Windows Credential Manager, macOS Keychain, or the Secret Service on Linux.
Tokens are never written to settings, logs or the job history.
"""

from __future__ import annotations

import os

SERVICE = "IrisEcho"
HF = "huggingface"


def _keyring():
    import keyring

    return keyring


def get(name: str = HF) -> str | None:
    env = os.environ.get("HF_TOKEN") if name == HF else None
    if env:
        return env
    try:
        return _keyring().get_password(SERVICE, name)
    except Exception:
        return None


def store(name: str, value: str) -> None:
    _keyring().set_password(SERVICE, name, value)


def delete(name: str) -> None:
    try:
        _keyring().delete_password(SERVICE, name)
    except Exception:
        pass


def available() -> bool:
    try:
        backend = _keyring().get_keyring()
        return "fail" not in type(backend).__module__
    except Exception:
        return False
