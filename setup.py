import setuptools
import re

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()
with open("taskkit/version.py", "r", encoding="utf-8") as fh:
    version = re.match("VERSION = '(.*)'", fh.read()).groups()[0]

setuptools.setup(
    name="taskkit",
    version=version,
    author="Ryosuke Sasaki",
    author_email="saryou.ssk@gmail.com",
    description="a distributed task runner",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/saryou/taskkit",
    project_urls={
        "Bug Tracker": "https://github.com/saryou/taskkit/issues",
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    packages=setuptools.find_packages(exclude=["tests"]),
    package_dir={"taskkit": "taskkit"},
    package_data={"taskkit": ["py.typed"]},
    python_requires=">=3.9",
    install_requires=[
        'typing-extensions>=4.0.0',
    ]
)
