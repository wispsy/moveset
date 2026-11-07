"""Install both artifacts outside the checkout and run public contract tests."""

from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
from email.parser import BytesParser

ROOT = Path(__file__).resolve().parents[1]


def main():
    directory = Path(sys.argv[1]).resolve()
    artifacts = sorted([*directory.glob("*.whl"), *directory.glob("*.tar.gz")])
    assert len(artifacts) == 2
    wheel, = directory.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert "moveset/py.typed" in archive.namelist()
        metadata, = [name for name in archive.namelist() if name.endswith("/METADATA")]
        message = BytesParser().parsebytes(archive.read(metadata))
        assert message["Name"] == "moveset"
        assert message["Requires-Python"] == ">=3.11"
        assert all("func-seq" not in item or "extra ==" in item for item in message.get_all("Requires-Dist", []))
    for artifact in artifacts:
        with tempfile.TemporaryDirectory() as temporary:
            outside = Path(temporary)
            environment = outside / "env"
            subprocess.run(["uv", "venv", str(environment), "--python", sys.executable], check=True)
            python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            subprocess.run(["uv", "pip", "install", "--python", str(python), str(artifact)], check=True, cwd=outside)
            subprocess.run([str(python), "-I", "-c", "import importlib.util, moveset; assert importlib.util.find_spec('func_seq') is None"], check=True, cwd=outside)
            subprocess.run([str(python), "-I", "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-v"], check=True, cwd=outside)
            lifecycle = ROOT / "lifecycle_tests"
            if sys.version_info >= (3, 14) and lifecycle.exists():
                subprocess.run(["uv", "pip", "install", "--python", str(python), f"{artifact}[sequence]"], check=True, cwd=outside)
                subprocess.run([str(python), "-I", "-m", "unittest", "discover", "-s", str(lifecycle), "-v"], check=True, cwd=outside)
            subprocess.run(["uv", "pip", "check", "--python", str(python)], check=True, cwd=outside)


if __name__ == "__main__":
    main()
