from pathlib import Path

from setuptools import find_packages, setup


readme = Path(__file__).with_name("README.md").read_text(encoding="utf-8")

setup(
    name="codex-capacity-fallback",
    version="0.1.0",
    description="Bounded model fallback for Codex CLI capacity errors",
    long_description=readme,
    long_description_content_type="text/markdown",
    license="MIT",
    author="liebesu",
    python_requires=">=3.9",
    packages=find_packages("src"),
    package_dir={"": "src"},
    extras_require={"test": ["pytest>=8.0"]},
    entry_points={"console_scripts": ["codex-capacity-fallback=codex_capacity_fallback.cli:main"]},
    keywords=["codex", "openai", "llm", "fallback", "capacity", "retry"],
    classifiers=[
        "Development Status :: 3 - Alpha",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
    ],
)
