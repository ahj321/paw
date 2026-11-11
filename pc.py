#!/usr/bin/env python3
"""兼容入口：主程序已更名为 paw.py，这里只是转发，旧脚本里的 pc.py 仍然能用。"""

import sys

import paw

if __name__ == "__main__":
    sys.exit(paw.main())
