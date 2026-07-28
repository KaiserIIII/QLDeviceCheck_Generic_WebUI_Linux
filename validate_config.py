#!/usr/bin/env python3
"""Validate config/device_list.json without accessing hardware."""

import sys

from core.config_manager import StandardDeviceConfig


def main() -> int:
    try:
        config = StandardDeviceConfig()
    except (OSError, ValueError) as exc:
        print(f"配置校验失败:\n{exc}")
        return 1

    direct = len(config.raw_devices())
    children = len(config.child_device_templates())
    print("配置校验通过")
    print(f"工控机编号: {config.workstation_id()}")
    print(f"顶层设备: {direct}")
    print(f"子设备: {children}")
    print(f"总测试项: {direct + children}")
    print(f"写测试: {'已启用' if config.allow_write_tests() else '已禁用'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
