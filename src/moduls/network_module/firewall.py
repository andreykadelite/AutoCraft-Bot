import hashlib
from typing import List

from .utils import run_shell_command

RULE_PREFIX = "BOT_NET"


def _hash_value(value: str) -> str:
    return hashlib.sha1(value.lower().encode("utf-8")).hexdigest()[:10]


def _rule_name_app(path: str) -> str:
    return f"{RULE_PREFIX}_APP_{_hash_value(path)}"


def _rule_name_port(port: int, proto: str) -> str:
    return f"{RULE_PREFIX}_PORT_{port}_{proto.upper()}"


def block_app(path: str) -> str:
    rule = _rule_name_app(path)
    cmds = [
        f'netsh advfirewall firewall add rule name="{rule}" dir=out action=block program="{path}" enable=yes',
        f'netsh advfirewall firewall add rule name="{rule}" dir=in action=block program="{path}" enable=yes',
    ]
    errors: List[str] = []
    for cmd in cmds:
        stdout, stderr, code = run_shell_command(cmd)
        if code != 0:
            errors.append(stderr or stdout or f"код {code}")
    if errors:
        return f"Правило для {path} частично/не создано: {'; '.join(errors)}"
    return f"Приложение {path} заблокировано (правило {rule})."


def unblock_app(path: str) -> str:
    rule = _rule_name_app(path)
    stdout, stderr, code = run_shell_command(f'netsh advfirewall firewall delete rule name="{rule}"')
    if code != 0:
        return f"Не удалось удалить правило {rule}: {stderr or stdout or f'код {code}'}"
    return f"Правила для приложения {path} удалены."


def block_port(port: int, proto: str = "TCP") -> str:
    rule = _rule_name_port(port, proto)
    cmds = [
        f'netsh advfirewall firewall add rule name="{rule}" dir=out action=block protocol={proto} localport={port} enable=yes',
        f'netsh advfirewall firewall add rule name="{rule}" dir=in action=block protocol={proto} localport={port} enable=yes',
    ]
    errors: List[str] = []
    for cmd in cmds:
        stdout, stderr, code = run_shell_command(cmd)
        if code != 0:
            errors.append(stderr or stdout or f"код {code}")
    if errors:
        return f"Порт {port}/{proto} частично/не заблокирован: {'; '.join(errors)}"
    return f"Порт {port}/{proto} заблокирован (правило {rule})."


def unblock_port(port: int, proto: str = "TCP") -> str:
    rule = _rule_name_port(port, proto)
    stdout, stderr, code = run_shell_command(f'netsh advfirewall firewall delete rule name="{rule}"')
    if code != 0:
        return f"Не удалось удалить правило {rule}: {stderr or stdout or f'код {code}'}"
    return f"Правило для порта {port}/{proto} удалено."


def list_bot_rules() -> str:
    cmd = 'netsh advfirewall firewall show rule name=all | findstr BOT_NET'
    stdout, stderr, code = run_shell_command(cmd)
    if code != 0 and not stdout:
        return f"Не удалось получить правила: {stderr or stdout or f'код {code}'}"
    if not stdout:
        return "Правила с префиксом BOT_NET не найдены."
    return "Правила бота в брандмауэре:\n" + stdout
