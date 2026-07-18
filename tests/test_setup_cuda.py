from pathlib import Path


def test_cuda_library_path_discovers_chatterbox_runtime_libraries(tmp_path: Path):
    from richard.setup.cuda import cuda_library_path

    site = tmp_path / "lib" / "python3.10" / "site-packages" / "nvidia"
    cublas = site / "cublas" / "lib"
    cudnn = site / "cudnn" / "lib"
    cublas.mkdir(parents=True)
    cudnn.mkdir(parents=True)

    assert cuda_library_path(tmp_path) == f"{cublas}:{cudnn}"


def test_cuda_library_path_is_empty_without_nvidia_packages(tmp_path: Path):
    from richard.setup.cuda import cuda_library_path

    assert cuda_library_path(tmp_path) == ""
