"""引擎客户端与 stub 引擎的行为规格。

这一层是接缝：工作台对引擎说的每句话都在这里兑现，所以测试**不 mock 客户端**——
HTTP 序列化、轮询、错误还原全部真的走一遍（对着 `lab.stub.StubEngine`）。
"""

from __future__ import annotations

import socket

import pytest

from lab.comfy import (
    EngineClient,
    EngineRejected,
    EngineTimeout,
    EngineUnreachable,
)
from lab.stub import STUB_FILENAME, StubEngine


def workflow() -> dict:
    return {"3": {"class_type": "UNETLoader", "inputs": {"unet_name": "x.safetensors"}}}


def dead_port() -> int:
    """占一个端口再放掉 —— 大概率不会有人监听。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def client(
    engine: StubEngine, *, max_polls: int = 50, poll_interval: float = 0.01
) -> EngineClient:
    """测试用的客户端：轮询快、放弃早，免得把测试拖成秒级。"""
    return EngineClient(
        engine.host,
        engine.port,
        poll_interval=poll_interval,
        max_polls=max_polls,
    )


# ---------- 提交 ----------


def test_submit_returns_the_prompt_id():
    with StubEngine() as engine:
        prompt_id = client(engine).submit(workflow())

    assert prompt_id


def test_submit_counts_as_a_submission_on_the_engine():
    with StubEngine() as engine:
        client(engine).submit(workflow())

        assert engine.submitted == 1


def test_version_reads_the_engine_banner():
    with StubEngine() as engine:
        assert client(engine).version() == "0.0.0-stub"


# ---------- 全流程 ----------


def test_generate_walks_submit_poll_and_fetch(tmp_path):
    with StubEngine() as engine:
        result = client(engine).generate(workflow(), tmp_path)

    assert result.prompt_id
    assert [f.name for f in result.files] == [STUB_FILENAME]


def test_generate_waits_through_empty_history(tmp_path):
    """stub 前两次查询故意回空 —— 客户端必须真的等，不能查一次就放弃。"""
    with StubEngine(ticks_before_done=2) as engine:
        result = client(engine).generate(workflow(), tmp_path)

    assert result.files


def test_fetched_file_has_the_engine_bytes(tmp_path):
    with StubEngine() as engine:
        result = client(engine).generate(workflow(), tmp_path)

    assert result.files[0].read_bytes() == engine.image


def test_generated_file_lands_where_asked(tmp_path):
    target = tmp_path / "output" / "2026-10-22"
    with StubEngine() as engine:
        client(engine).generate(workflow(), target)

    assert (target / STUB_FILENAME).exists()


def test_result_carries_the_engine_history_entry(tmp_path):
    """留证据：结果里带着引擎的原始回复，出了问题能回头看。"""
    with StubEngine() as engine:
        result = client(engine).generate(workflow(), tmp_path)

    assert result.history["status"]["status_str"] == "success"
    assert result.images[0].node == "9"


# ---------- 出错 ----------


def test_unreachable_engine_raises_a_readable_error():
    with pytest.raises(EngineUnreachable) as caught:
        EngineClient("127.0.0.1", dead_port(), poll_interval=0.01).submit(workflow())

    message = str(caught.value)
    assert "连不上" in message
    assert "ComfyUI" in message


def test_unreachable_engine_does_not_leak_a_socket_error():
    with pytest.raises(EngineUnreachable) as caught:
        EngineClient("127.0.0.1", dead_port(), poll_interval=0.01).submit(workflow())

    assert not isinstance(caught.value, OSError)


def test_rejected_prompt_is_reported_with_node_details():
    with (
        StubEngine(reject_prompts=True) as engine,
        pytest.raises(EngineRejected) as caught,
    ):
        client(engine).submit(workflow())

    message = str(caught.value)
    assert "拒绝" in message
    assert "UNETLoader" in message  # 哪个节点出的问题要说出来
    assert "flux-2-klein-9b-fp8.safetensors" in message


def test_polling_gives_up_after_max_polls(tmp_path):
    """引擎永远不报完成时，客户端不能无限等下去。"""
    with (
        StubEngine(ticks_before_done=9999) as engine,
        pytest.raises(EngineTimeout) as caught,
    ):
        client(engine, max_polls=3).generate(workflow(), tmp_path)

    assert "3" in str(caught.value)


# ---------- 边界 ----------


def test_client_has_no_way_to_start_the_engine():
    """ADR 0004：工作台不负责启动引擎进程。"""
    for name in ("start", "launch", "spawn", "start_engine", "ensure_running"):
        assert not hasattr(EngineClient, name)


def test_stub_reports_itself_as_a_stub():
    """stub 不该自称真引擎版本号，免得被人当成真环境。"""
    with StubEngine() as engine:
        assert "stub" in client(engine).version()
