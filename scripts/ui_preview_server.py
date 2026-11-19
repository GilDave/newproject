import sys, threading, http.server, time
from pathlib import Path as _P; _R = _P(__file__).resolve().parent.parent; sys.path.insert(0, str(_R)); sys.path.insert(0, str(_R / "tests"))
from helpers import make_repo
from pathlib import Path
import tempfile
from solomon.dashboard import DashboardApp, make_handler
from solomon.settings import Settings
from http.server import ThreadingHTTPServer
d = Path(tempfile.mkdtemp())
repo = make_repo(d)
app = DashboardApp(Settings(), [str(repo)], store_path=d / "store.json", launcher=lambda a, l: None)
app.providers_info = lambda: [{"name": n, "installed": True, "supports_write": n != "ollama", "default": n != "ollama", "local": n == "ollama", "model": "qwen3:8b" if n == "ollama" else ""} for n in ("claude", "codex", "antigravity", "ollama")]
hosts = set()
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
srv = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(app, "tok", hosts))
hosts.update({f"127.0.0.1:{PORT}", f"localhost:{PORT}"})
print("ready", flush=True)
srv.serve_forever()
