"""A smaller copy of a GLB model for servers with a file size limit (the Supabase cloud: 50 MB).

The Raspberry Pi stores the full-quality Tripo model; the cloud gets a copy whose mesh is
simplified until the file fits (glb_tools/shrink.mjs: meshoptimizer simplifier through glTF-Transform,
textures kept, no compression extensions, so the app's 3D viewer loads it).

Needs Node.js and the tool's packages once:  cd scripts/scripts/glb_tools && npm ci
"""
import json
import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "glb_tools", "shrink.mjs")
# A Supabase Free-plan project refuses files over 50 MiB; aim a little lower.
CLOUD_FILE_LIMIT = 50 * 1024 * 1024
CLOUD_TARGET_BYTES = 47_000_000


def node_binary():
    return shutil.which("node") or next(
        (p for p in (os.path.expanduser("~/.local/bin/node"), "/usr/bin/node") if os.path.exists(p)), None)


def shrink_glb(glb, max_bytes=CLOUD_TARGET_BYTES, log=print, timeout=600):
    """``glb`` reduced to at most ``max_bytes``, or ``None`` when that is not possible.

    A model that already fits is returned unchanged. Missing Node.js or packages, a failing tool or a
    limit that cannot be reached give ``None`` with one log line: the caller then stores no model on
    that server, as before.
    """
    if len(glb) <= max_bytes:
        return glb
    node = node_binary()
    if node is None:
        log("  Уменьшение модели невозможно: не найден Node.js.")
        return None
    if not os.path.isdir(os.path.join(HERE, "glb_tools", "node_modules")):
        log("  Уменьшение модели невозможно: выполните один раз  cd scripts/scripts/glb_tools && npm ci")
        return None
    with tempfile.TemporaryDirectory(prefix="archoai-glb-") as tmp:
        source, target = os.path.join(tmp, "in.glb"), os.path.join(tmp, "out.glb")
        with open(source, "wb") as handle:
            handle.write(glb)
        try:
            done = subprocess.run([node, TOOL, source, target, str(int(max_bytes))],
                                  capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log(f"  Уменьшение модели не удалось: {exc}")
            return None
        if done.returncode != 0 or not os.path.exists(target):
            log(f"  Уменьшение модели не удалось: {(done.stderr or done.stdout).strip()[-300:]}")
            return None
        with open(target, "rb") as handle:
            smaller = handle.read()
    try:
        info = json.loads(done.stdout.strip().splitlines()[-1])
        log(f"  Копия для облака: {info['outputBytes'] / 1e6:.1f} МБ, {info['outputFaces']:,} граней "
            f"(оригинал {info['inputBytes'] / 1e6:.1f} МБ, {info['inputFaces']:,} граней).")
    except (ValueError, KeyError, IndexError):
        pass
    return smaller if len(smaller) <= max_bytes else None


def model_for_server(name, glb, cache, shrink=None):
    """The model file for one server: the Raspberry Pi always gets the full-quality original; a
    server with the 50 MB limit (the Supabase cloud) gets a simplified copy when the original is
    larger. The copy is made once per scan (``cache``, a dict). ``None``: no model for that server."""
    if glb is None or name == "Raspberry Pi" or len(glb) <= CLOUD_FILE_LIMIT:
        return glb
    if "copy" not in cache:
        print(f"  Модель {len(glb) / 1e6:.1f} МБ больше 50 МБ: уменьшаю копию для облака...")
        cache["copy"] = (shrink or shrink_glb)(glb)
    return cache["copy"]
