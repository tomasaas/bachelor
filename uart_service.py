from __future__ import annotations

import time
from typing import Any, Dict


def send_uart_command(
    command: str,
    serial_module,
    port: str,
    baud: int,
    timeout: float,
) -> Dict[str, Any]:
    if serial_module is None:
        raise RuntimeError("pyserial is not installed")

    command = command.strip()
    if not command:
        raise ValueError("Command is empty")

    payload = (command + "\n").encode("ascii", errors="ignore")
    with serial_module.Serial(port, baud, timeout=timeout) as connection:
        connection.write(payload)
        connection.flush()
        time.sleep(0.15)
        response_bytes = connection.read_all()

    response = response_bytes.decode("ascii", errors="ignore").strip() if response_bytes else ""
    return {
        "port": port,
        "baud": baud,
        "sent": command,
        "response": response,
    }
