"""Runs Python code from the chat ("Code-Modus") in a throw-away Docker container and returns the output and plots.

Each run is a new container from the image in code.image (built from deploy/sandbox/Dockerfile) with:
no network, a read-only root filesystem, CPU, memory and process limits, no Linux capabilities, user "nobody",
and a time limit after which the container is killed. The code goes in through stdin; the working directory /work
is a small tmpfs inside the container, so nothing is written on the host and everything is gone after the run.

Who may run code: admins, and everyone else only with code.allow_users (config.json, admins only).
"""
import asyncio
import json
import secrets
import shutil

MARKER = "\x1e__LOCAL_COUNCIL_RESULT__"
MAX_OUTPUT = 5 * 1024 * 1024  # bytes read from the container at most
_running = asyncio.Semaphore(2)  # code runs on the CPU; more at once would slow everything down

# Runs inside the container (python -I -c RUNNER): executes the code from stdin in /work, captures stdout/stderr
# (cut at 100 kB), saves matplotlib figures (plt.show() and figures still open at the end) and PNG files the code
# wrote, and prints everything as JSON after MARKER.
RUNNER = r'''
import base64, contextlib, glob, io, json, os, signal, sys, traceback
code = sys.stdin.read()
def on_alarm(*_):
    raise TimeoutError(f"Zeitlimit von {os.environ['LC_TIMEOUT']} s überschritten")
signal.signal(signal.SIGALRM, on_alarm)
signal.alarm(int(os.environ["LC_TIMEOUT"]))
out, err = io.StringIO(), io.StringIO()
images = []
def save_figures():
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return
    for n in plt.get_fignums():
        buf = io.BytesIO()
        plt.figure(n).savefig(buf, format="png", dpi=100, bbox_inches="tight")
        images.append(base64.b64encode(buf.getvalue()).decode())
    plt.close("all")
try:
    import shutil
    if os.path.isdir("/opt/mpl"):  # font cache built into the image; matplotlib would otherwise rebuild it each run
        shutil.copytree("/opt/mpl", "/tmp/mpl", dirs_exist_ok=True)
        os.environ["MPLCONFIGDIR"] = "/tmp/mpl"
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.show = lambda *a, **k: save_figures()
except Exception:
    pass
error = None
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    try:
        exec(compile(code, "main.py", "exec"), {"__name__": "__main__"})
    except SystemExit:
        pass
    except BaseException:
        error = traceback.format_exc(limit=-8)
signal.alarm(0)
save_figures()
for path in sorted(glob.glob("/work/**/*.png", recursive=True))[:5]:
    if os.path.getsize(path) < 2_000_000:
        images.append(base64.b64encode(open(path, "rb").read()).decode())
cut = lambda s: s if len(s) <= 100_000 else s[:100_000] + "\n… (gekürzt)"
sys.__stdout__.write("MARKER" + json.dumps({"stdout": cut(out.getvalue()), "stderr": cut(err.getvalue()),
                                             "error": error, "images": images[:8]}))
'''.replace("MARKER", MARKER.replace("\x1e", "\\x1e"))


class SandboxError(RuntimeError):
    pass


def settings(cfg: dict) -> dict:
    c = cfg.get("code", {})
    return {"docker": c.get("docker", "docker"), "image": c.get("image", "local-council-sandbox:1"),
            "timeout": int(c.get("timeout", 30)), "cpus": float(c.get("cpus", 1)),
            "memory_mb": int(c.get("memory_mb", 512)), "allow_users": bool(c.get("allow_users", False))}


def command(s: dict, name: str) -> list[str]:
    return [
        s["docker"], "run", "--rm", "-i", "--name", name,
        "--network", "none",
        "--read-only", "--tmpfs", "/work:rw,size=64m,mode=1777", "--tmpfs", "/tmp:rw,size=64m,mode=1777",
        "--cpus", str(s["cpus"]), "--memory", f"{s['memory_mb']}m", "--memory-swap", f"{s['memory_mb']}m",
        "--pids-limit", "64", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "65534:65534", "--workdir", "/work",
        "-e", "HOME=/tmp", "-e", "MPLCONFIGDIR=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", f"LC_TIMEOUT={s['timeout']}",
        s["image"], "python", "-I", "-c", RUNNER,
    ]


async def _docker(s: dict, *args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(s["docker"], *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace").strip()


async def status(cfg: dict) -> dict:
    """Whether code can run here: Docker reachable and the image built. Never raises."""
    s = settings(cfg)
    if not shutil.which(s["docker"]):
        return {"available": False, "reason": "Docker ist nicht installiert."}
    try:
        code, out = await asyncio.wait_for(_docker(s, "image", "inspect", "--format", "{{.Id}}", s["image"]), 15)
    except (OSError, TimeoutError) as e:
        return {"available": False, "reason": f"Docker antwortet nicht: {e}"}
    if code == 0:
        return {"available": True, "reason": ""}
    if "permission denied" in out.lower() or "cannot connect" in out.lower():
        return {"available": False, "reason": "Die App darf Docker nicht benutzen (siehe README, Abschnitt Code-Modus)."}
    return {"available": False, "reason": f"Das Sandbox-Image {s['image']} fehlt (siehe README: docker build …)."}


async def run(cfg: dict, code: str) -> dict:
    """{"stdout", "stderr", "error", "images": [base64 PNG], "exit_code", "timed_out", "seconds"}."""
    s = settings(cfg)
    name = f"lc-sandbox-{secrets.token_hex(6)}"
    async with _running:
        loop = asyncio.get_running_loop()
        start = loop.time()
        try:
            proc = await asyncio.create_subprocess_exec(*command(s, name), stdin=asyncio.subprocess.PIPE,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            raise SandboxError(f"Docker konnte nicht gestartet werden: {e}") from e
        timed_out = False
        try:
            # The code stops itself after s["timeout"] (alarm in RUNNER); this is the backstop for code that ignores
            # signals, with some room for the container start.
            out, err = await asyncio.wait_for(proc.communicate(code.encode()), s["timeout"] + 10)
        except TimeoutError:
            timed_out = True
            await _docker(s, "kill", name)
            out, err = await proc.communicate()
        except asyncio.CancelledError:  # the user left: do not leave the container running
            await _docker(s, "kill", name)
            raise
        seconds = round(loop.time() - start, 2)
    text = out[:MAX_OUTPUT].decode(errors="replace")
    head, _, payload = text.partition(MARKER)
    result = {"stdout": head, "stderr": err[:100_000].decode(errors="replace"), "error": None, "images": []}
    if payload:
        try:
            data = json.loads(payload)
            result.update(stdout=head + data.get("stdout", ""), error=data.get("error"), images=data.get("images", []),
                          stderr=data.get("stderr", "") + result["stderr"])
        except ValueError:
            pass
    if timed_out:
        result["error"] = f"Abgebrochen: Zeitlimit von {s['timeout']} s überschritten."
    elif proc.returncode == 137 and not payload:
        result["error"] = f"Abgebrochen: Speicherlimit von {s['memory_mb']} MB überschritten."
    elif proc.returncode == 125:
        result["error"] = "Docker konnte den Container nicht starten: " + result["stderr"].strip()[:500]
        result["stderr"] = ""
    return {**result, "exit_code": proc.returncode, "timed_out": timed_out, "seconds": seconds}
