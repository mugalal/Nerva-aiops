"""Install the pinned kubectl release after checking its official SHA-256."""
import hashlib
import platform
from pathlib import Path
from urllib.request import urlopen


VERSION = "v1.37.0"
ARCH = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
if ARCH is None:
    raise RuntimeError(f"Unsupported kubectl architecture: {platform.machine()}")
URL = f"https://dl.k8s.io/release/{VERSION}/bin/linux/{ARCH}/kubectl"
DESTINATION = Path("/usr/local/bin/kubectl")

with urlopen(URL + ".sha256", timeout=60) as response:
    expected = response.read().decode("ascii").strip().split()[0]
if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
    raise RuntimeError("Official kubectl checksum is malformed")
with urlopen(URL, timeout=120) as response:
    binary = response.read()
if hashlib.sha256(binary).hexdigest() != expected:
    raise RuntimeError("kubectl SHA-256 does not match the official release checksum")
DESTINATION.write_bytes(binary)
DESTINATION.chmod(0o755)
