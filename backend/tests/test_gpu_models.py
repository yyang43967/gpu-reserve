from app.models import GpuModel
from app.models.server import GPU_MEMORY_GB


def test_every_gpu_model_has_a_memory_size() -> None:
    assert set(GPU_MEMORY_GB) == set(GpuModel)
    assert all(size > 0 for size in GPU_MEMORY_GB.values())


def test_memory_matches_model_names_that_state_it() -> None:
    assert GPU_MEMORY_GB[GpuModel.A100_40GB] == 40
    assert GPU_MEMORY_GB[GpuModel.A100_80GB] == 80
    assert GPU_MEMORY_GB[GpuModel.H100_80GB] == 80
