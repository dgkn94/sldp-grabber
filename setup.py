from setuptools import setup

import re
from pathlib import Path

here = Path(__file__).parent

with open(here / "README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open(here / "sldp_grabber" / "__init__.py", "r", encoding="utf-8") as fh:
    m = re.search(r'^__version__\s*=\s*[\'"]([^\'"]*)[\'"]', fh.read(), re.M)
    version = m.group(1) if m else "1.0.0"

setup(
    name="sldp-grabber",
    version=version,
    author="SLDP Grabber",
    description="A small tool and library to grab SLDP WebSocket streams and save/mux them.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/dgkn94/sldp-grabber",
    packages=["sldp_grabber"], 
    python_requires=">=3.7",
    install_requires=[
        "websocket-client>=1.3.0",
    ],
    entry_points={
        "console_scripts": [
            "sldp-grabber=sldp_grabber.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    keywords="sldp, websocket, streaming, h264, aac, opus",
)
