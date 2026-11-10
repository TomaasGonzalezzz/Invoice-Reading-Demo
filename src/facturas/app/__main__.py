"""Open the demo in the browser: python -m facturas.app [--port 8000] [--workers 2] [--no-browser]"""

from __future__ import annotations

import argparse
import threading
import webbrowser


def main() -> None:
    ap = argparse.ArgumentParser(description="Demo local: facturas a planilla controlada.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--workers", type=int, default=2, help="documentos que se leen a la vez")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    import uvicorn

    from . import server

    server.STATE["workers"] = max(1, args.workers)
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Demo en {url}  (Ctrl+C para cerrar)")
    uvicorn.run(server.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
