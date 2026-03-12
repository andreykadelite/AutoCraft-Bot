# -*- coding: utf-8 -*-
"""
sys_core package.
"""

__all__ = ["full_restart", "full_restart_from_message", "full_restart_via_bot"]


def full_restart(*args, **kwargs):
    from .full_restart import full_restart as _full_restart

    return _full_restart(*args, **kwargs)


def full_restart_from_message(*args, **kwargs):
    from .full_restart import full_restart_from_message as _full_restart_from_message

    return _full_restart_from_message(*args, **kwargs)


def full_restart_via_bot(*args, **kwargs):
    from .full_restart import full_restart_via_bot as _full_restart_via_bot

    return _full_restart_via_bot(*args, **kwargs)
