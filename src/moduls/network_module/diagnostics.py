from .utils import run_shell_command


def ping(host: str, count: int = 4) -> str:
    cmd = f"ping -n {count} {host}"
    stdout, stderr, code = run_shell_command(cmd)
    if code != 0 and not stdout:
        return f"Ping завершился с ошибкой: {stderr or f'код {code}'}"
    return stdout or stderr


def traceroute(host: str) -> str:
    cmd = f"tracert {host}"
    stdout, stderr, code = run_shell_command(cmd)
    if code != 0 and not stdout:
        return f"Traceroute завершился с ошибкой: {stderr or f'код {code}'}"
    return stdout or stderr


def flush_dns() -> str:
    stdout, stderr, code = run_shell_command("ipconfig /flushdns")
    if code != 0 and not stdout:
        return f"Сброс DNS завершился с ошибкой: {stderr or f'код {code}'}"
    return stdout or stderr


def renew_ip() -> str:
    stdout, stderr, code = run_shell_command("ipconfig /renew")
    if code != 0 and not stdout:
        return f"Renew завершился с ошибкой: {stderr or f'код {code}'}"
    return stdout or stderr


def release_ip() -> str:
    stdout, stderr, code = run_shell_command("ipconfig /release")
    if code != 0 and not stdout:
        return f"Release завершился с ошибкой: {stderr or f'код {code}'}"
    return stdout or stderr


def ipconfig_all() -> str:
    stdout, stderr, code = run_shell_command("ipconfig /all")
    if code != 0 and not stdout:
        return f"ipconfig /all завершился с ошибкой: {stderr or f'код {code}'}"
    return stdout or stderr
