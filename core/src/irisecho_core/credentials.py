# SPDX-License-Identifier: AGPL-3.0-or-later
"""Access tokens for model hosts, kept in the operating system's keychain.

Windows Credential Manager, macOS Keychain, or the Secret Service on Linux.
Tokens are never written to settings, logs or the job history.
"""

from __future__ import annotations

import os

from irisecho_core import paths

SERVICE = "IrisEcho"
HF = "huggingface"


def _keyring():
    import keyring

    return keyring


class NoKeychain(RuntimeError):
    """The token cannot be kept: this computer has no secure storage that works."""


_noted: set[str] = set()


def _note(what: str, e: Exception) -> None:
    """Log a keychain problem once per kind (these calls run on every page load)."""
    if what in _noted:
        return
    _noted.add(what)
    paths.note("credentials", f"Could not {what} a saved token: {type(e).__name__}")


def get(name: str = HF) -> str | None:
    env = os.environ.get("HF_TOKEN") if name == HF else None
    if env:
        return env
    try:
        return _keyring().get_password(SERVICE, name)
    except Exception as e:
        _note("read", e)
        return None


def source(name: str = HF) -> str | None:
    """Where the token in use comes from: "environment" (HF_TOKEN), "saved" (keychain) or None."""
    if name == HF and os.environ.get("HF_TOKEN"):
        return "environment"
    return "saved" if get(name) else None


def store(name: str, value: str) -> None:
    try:
        _keyring().set_password(SERVICE, name, value)
    except Exception as e:
        _note("save", e)
        raise NoKeychain(
            "This computer has no secure storage to keep the token in. "
            "Set the HF_TOKEN environment variable instead."
        ) from e


def delete(name: str) -> None:
    try:
        _keyring().delete_password(SERVICE, name)
    except Exception as e:
        _note("delete", e)


def available() -> bool:
    try:
        backend = _keyring().get_keyring()
        return "fail" not in type(backend).__module__
    except Exception as e:
        _note("look for", e)
        return False
