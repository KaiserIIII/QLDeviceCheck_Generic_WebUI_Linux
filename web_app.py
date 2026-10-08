#!/usr/bin/env python3
"""Compatibility entry point for the local field acceptance workbench."""
import argparse
import os
from pathlib import Path

from inspection.http import make_server
from inspection.service import InspectionService


def __getattr__(name):
    # Preserve explicitly requested Python helpers without importing hardware in demo.
    if name == 'preserve_scan_interfaces':
        from legacy_web import preserve_scan_interfaces
        return preserve_scan_interfaces
    raise AttributeError(name)


def main():
    parser = argparse.ArgumentParser(description='Field acceptance workbench')
    parser.add_argument('--demo', action='store_true', help='Synthetic devices; never access hardware')
    parser.add_argument('--data-dir', default=str(Path(__file__).resolve().parent / 'data'))
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8080, type=int)
    parser.add_argument('--config', default=None)
    args = parser.parse_args()
    service = None
    server = None
    try:
        service = InspectionService(Path(args.data_dir) / 'jobs.db', demo=args.demo, config_path=args.config)
        server = make_server(args.host, args.port, service, token=os.environ.get('QLDC_ACCESS_TOKEN', ''))
        print('SIMULATED demo mode' if args.demo else 'LIVE field mode')
        print(f'Workbench: http://{args.host}:{args.port}')
        server.serve_forever()
    except KeyboardInterrupt:
        print('Shutting down...')
    except Exception:
        print('Startup or operation failed. Check configuration, port and QLDC_ACCESS_TOKEN.')
        return 1
    finally:
        if server:
            server.server_close()
        if service:
            service.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
