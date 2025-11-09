"""Setup script for sldp-grabber (legacy, prefer pyproject.toml)."""

import re
from pathlib import Path

from setuptools import setup, find_packages

README = Path("README.md").read_text(encoding="utf-8")

version_file = Path("sldp_grabber/__init__.py").read_text(encoding="utf-8")
version_match = re.search(r'^__version__\s*=\s*[\'"]([^\'"]*)[\'"]', version_file, re.M)
version = version_match.group(1) if version_match else "1.0.0"

setup(
    name="sldp-grabber",
    version=version,
    author="SLDP Grabber",
    description="A small tool and library to grab SLDP WebSocket streams and save/mux them.",
    long_description=README,
    long_description_content_type="text/markdown",
    url="https://github.com/dgkn94/sldp-grabber",
    packages=find_packages(),
    python_requires=">=3.7",
    install_requires=[
        "websocket-client>=1.3.0",
    ],
    entry_points={
        "console_scripts": [
            "sldp-grabber=sldp_grabber.cli:main",
        ],
    },
    keywords="sldp, websocket, streaming, h264, aac, opus",
)
