#!/usr/bin/env python3
"""Install pinned, checksum-verified deployment validation tools."""
import argparse
import hashlib
import io
from pathlib import Path
import platform
import tarfile
import urllib.request

CHECKSUMS = {
    'linux_amd64': ('cb9e31198d3f63b44848bd0afc4c8efc56e6cfef9c93a4a39a211940e6decd61', '9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883'),
    'linux_arm64': ('9867b76482cfc6d25b546abc1d2b5a32bd137505aba640649b4b378f64f6d68f', '1f53fc8e81258197a35e8603054162a5af1de8c5af13746c71ab680d9534ed87'),
    'darwin_amd64': ('d08fb85846c3d977f0e2a66e7ff45f1b5264c294685e5e726e1a8bdfe0da8dee', '71dbc87ac9f24099a62b93570e65aa06312ba6ac8aea63b7f86e9d999edf5a92'),
    'darwin_arm64': ('4cbb08e209d8fff47cd60bfb6407daaf5ee699f9b9295849a1065024fac2d1ef', 'f84f4dfbebf4a6b0b230385fa065a39ea35e02608c2b50d025dcf64775a69d67'),
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    architecture = {'x86_64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine())
    target = platform.system().lower() + '_' + str(architecture)
    if target not in CHECKSUMS:
        parser.error('Install kustomize 5.8.3 and kubeconform 0.8.0 manually on this platform')
    args.output.mkdir(parents=True, exist_ok=True)
    artifacts = [
        ('kustomize', 'https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/v5.8.3/kustomize_v5.8.3_' + target + '.tar.gz'),
        ('kubeconform', 'https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-' + target.replace('_', '-') + '.tar.gz'),
    ]
    for (name, url), expected in zip(artifacts, CHECKSUMS[target]):
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError('Checksum mismatch for ' + name)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            binary = archive.extractfile(name).read()
        path = args.output / name
        path.write_bytes(binary)
        path.chmod(0o755)
        print('Installed ' + str(path))

if __name__ == '__main__':
    main()
