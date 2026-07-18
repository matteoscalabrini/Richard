from richard.setup.units import render_chatterbox_unit


def test_chatterbox_unit_has_workdir_gpu_and_execstart():
    text = render_chatterbox_unit(
        workdir="/opt/voice/Chatterbox-TTS-Server",
        python="/opt/voice/venv/bin/python",
        gpu="1",
    )
    assert "WorkingDirectory=/opt/voice/Chatterbox-TTS-Server" in text
    assert "Environment=CUDA_VISIBLE_DEVICES=1" in text
    assert "ExecStart=/opt/voice/venv/bin/python server.py" in text
    assert "WantedBy=multi-user.target" in text


def test_units_module_has_no_standalone_whisper_renderer():
    import richard.setup.units as units

    assert not hasattr(units, "render_whisper_unit")
