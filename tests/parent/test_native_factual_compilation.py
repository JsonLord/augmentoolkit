"""The allowlisted native module must compile without requiring its heavy extras."""

from pathlib import Path


def test_native_factual_module_compiles():
    path = (
        Path(__file__).resolve().parents[2]
        / "generation/core_pipelines/factual_generation_individual/factual_generation.py"
    )
    compile(path.read_text(), str(path), "exec")
