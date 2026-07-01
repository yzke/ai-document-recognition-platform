#!/usr/bin/env python3
import argparse

from ocr_app.routes import create_app, run_smoke


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--smoke")
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    if args.smoke:
        run_smoke(args.smoke, args.pages, args.dpi, args.workers)
        return
    create_app().run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
