"""Reproducible allowlist packaging. Never reads old project data or user runtime data."""
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = "1.0"


def archive(source, target, prefix):
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(source.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                z.write(p, str(Path(prefix) / p.relative_to(source)))


def notices(dist):
    output = dist / "licenses"
    output.mkdir(exist_ok=True)
    entries = []
    for package in metadata.distributions():
        name = package.metadata.get("Name", "unknown")
        entries.append("%s==%s | %s" % (name, package.version, package.metadata.get("Home-page", "")))
        for item in package.files or []:
            if "license" in str(item).lower() or "copying" in item.name.lower():
                path = Path(package.locate_file(item))
                if path.is_file() and path.stat().st_size < 3 * 1024**2:
                    filename = name + "-" + str(item).replace("/", "_").replace("\\", "_").replace(":", "_")
                    shutil.copy2(path, output / filename)
    pylicense = Path(sys.base_prefix) / "LICENSE.txt"
    if pylicense.exists(): shutil.copy2(pylicense, output / "Python-LICENSE.txt")
    (dist / "THIRD_PARTY_NOTICES.txt").write_text("Dependencies and build-tool notices. Some notices cover tools not shipped at runtime.\n"
        + "\n".join(sorted(entries)) + "\nSee licenses/ for original license terms. pystray source: https://github.com/moses-palmer/pystray\n", encoding="utf-8")


def main():
    release = ROOT / "release"
    release.mkdir(exist_ok=True)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--uac-admin", "--name", "LingSuoBasic",
               "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
               "--add-data", str(ROOT / "resources") + ";resources", "--collect-submodules", "botpy.types",
               "--hidden-import", "botpy", "--exclude-module", "botpy.ext.cog_apscheduler",
               "--hidden-import", "numpy", "--hidden-import", "cv2", "--collect-all", "cv2",
               "--hidden-import", "docx", "--hidden-import", "lxml.etree", "--hidden-import", "psutil", "--exclude-module", "lingsuo.panel",
               "--exclude-module", "onnxruntime", "--exclude-module", "rapidocr_onnxruntime", str(ROOT / "main.py")]
    subprocess.run(command, cwd=ROOT, check=True)
    dist = ROOT / "dist/LingSuoBasic"
    for name in ("README.md", "config.example.json", "requirements-lock.txt"):
        shutil.copy2(ROOT / name, dist / name)
    shutil.copytree(ROOT / "sample_plugins", dist / "sample_plugins", dirs_exist_ok=True)
    notices(dist)
    with tempfile.TemporaryDirectory(prefix="lingsuo-exe-test-") as temporary:
        report = Path(temporary) / "selftest.json"
        # The executable runs from an unrelated directory with no legacy project in PYTHONPATH.
        env = os.environ.copy(); env.pop("PYTHONPATH", None); env.pop("PYTHONHOME", None)
        subprocess.run([str(dist / "LingSuoBasic.exe"), "--self-test", "--report", str(report)], cwd=temporary,
                       env=env, timeout=90, check=True)
        result = json.loads(report.read_text(encoding="utf-8"))
        if result.get("ok") is not True or result.get("real_qq") is not False:
            raise RuntimeError("Isolated executable validation failed")
        (release / "exe-selftest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    forbidden = {"config.json", "state.sqlite3", "accounts.json", "monitor_config.json", "optional_adapters", "legacy_cherry"}
    bad = [str(p.relative_to(dist)) for p in dist.rglob("*") if p.name in forbidden or p.suffix in (".log", ".upload")]
    if bad: raise RuntimeError("Private data or adapters found in release: " + str(bad))
    manifest = {"version": VERSION, "signed": False, "real_qq_tested": False, "user_data_included": False,
                "optional_adapters_included": False, "files": {p.relative_to(dist).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(dist.rglob("*")) if p.is_file() and p.name != "release-manifest.json"}}
    (dist / "release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive(dist, release / ("LingSuoBasic-" + VERSION + "-Windows-x64.zip"), "LingSuoBasic")
    archive(ROOT / "optional_adapters", release / ("LingSuoBasic-" + VERSION + "-optional-adapters.zip"), "optional_adapters")
    source_zip = release / ("LingSuoBasic-" + VERSION + "-source.zip")
    with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED) as z:
        # ui is retained only for historical web regression tests in the source archive.
        for name in ("lingsuo", "resources", "ui", "tests", "docs", "sample_plugins"):
            for p in sorted((ROOT / name).rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts:
                    z.write(p, "LingSuoBasic/" + p.relative_to(ROOT).as_posix())
        for name in ("main.py", "bootstrap.py", "build_release.py", "README.md", "config.example.json", "requirements-lock.txt", "start.bat", ".gitignore"):
            z.write(ROOT / name, "LingSuoBasic/" + name)
    for p in release.glob("*.zip"):
        p.with_suffix(".zip.sha256.txt").write_text(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n", encoding="ascii")
    print("Release packages created in", release)


if __name__ == "__main__":
    main()
