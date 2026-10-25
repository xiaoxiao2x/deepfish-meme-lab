"""设置页写到磁盘的那一层（M5.5）：就地改 local.toml、把地址拆开、把选择记下来。

这里一个 HTTP 都不碰 —— 接口那一层在 tests/test_web_settings.py。这儿的判据是
"写回去之后那个文件还认不认"：注释还在不在、别的键有没有被顺手动过、
多行数组有没有被替换半个。写坏了的还原也在这儿测：写回一半的配置比没写更糟。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from lab.config import ConfigError, load_config
from lab.settings import (
    THEME_LABELS,
    Settings,
    parse_address,
    workflow_choices,
    write_local,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT = """
[comfy]
host = "127.0.0.1"
port = 8188
timeout = 600

[paths]
output_dir = "output"
asset_roots = []
asset_index = "assets/index.local.json"

[workflows]
default = "fish-txt2img.json"
img2img = "fish-img2img.json"

[generate]
count = 4
steps = 50
cfg = 3.5
width = 1024
height = 1024
denoise = 0.45
prompt_prefix = ""
negative = ""
"""

#: 一份像真本机配置的 local.toml：有注释、有别的键、有一个多行数组。
LOCAL = """# 本机配置 —— 不进 git。
[comfy]
# ComfyUI 装在哪
root = "/opt/ComfyUI"

[paths]
# 素材池：只读引用
asset_roots = [
  "/tmp/one",
  "/tmp/two",
]

[workflows]
img2img = "fish-img2img.json"

[postprocess]
font = "/fonts/x.ttc"
"""


def _repo(tmp_path: Path, *, local: str | None = None) -> Path:
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "default.toml").write_text(DEFAULT, encoding="utf-8")
    if local is not None:
        (tmp_path / "config" / "local.toml").write_text(local, encoding="utf-8")
    (tmp_path / "workflows").mkdir(exist_ok=True)
    for name in ("fish-txt2img.json", "fish-img2img.json"):
        (tmp_path / "workflows" / name).write_text("{}", encoding="utf-8")
    return tmp_path


def _local(repo: Path) -> dict:
    return tomllib.loads((repo / "config" / "local.toml").read_text(encoding="utf-8"))


# ---------- 就地写回 ----------


def test_write_local_replaces_one_line_and_keeps_the_comments(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    path.write_text(LOCAL, encoding="utf-8")

    write_local(path, {"comfy": {"port": 9999}})

    text = path.read_text(encoding="utf-8")
    assert "port = 9999" in text
    assert "# ComfyUI 装在哪" in text
    assert 'root = "/opt/ComfyUI"' in text
    assert "# 素材池：只读引用" in text
    assert 'font = "/fonts/x.ttc"' in text


def test_write_local_replaces_a_multiline_array_whole(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    path.write_text(LOCAL, encoding="utf-8")

    write_local(path, {"paths": {"asset_roots": ["/tmp/three"]}})

    text = path.read_text(encoding="utf-8")
    assert "/tmp/one" not in text
    assert "/tmp/two" not in text
    assert tomllib.loads(text)["paths"]["asset_roots"] == ["/tmp/three"]


def test_write_local_adds_a_key_the_section_does_not_have(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    path.write_text(LOCAL, encoding="utf-8")

    write_local(path, {"workflows": {"default": "fish-img2img.json"}})

    text = path.read_text(encoding="utf-8")
    assert tomllib.loads(text)["workflows"]["default"] == "fish-img2img.json"
    assert tomllib.loads(text)["workflows"]["img2img"] == "fish-img2img.json"
    assert text.index("[workflows]") < text.index("default =")


def test_write_local_appends_a_section_that_is_not_there(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    path.write_text(LOCAL, encoding="utf-8")

    write_local(path, {"ui": {"theme": "dark"}})

    text = path.read_text(encoding="utf-8")
    assert tomllib.loads(text)["ui"]["theme"] == "dark"
    assert text.rstrip().endswith('theme = "dark"')


def test_write_local_creates_the_file_when_there_is_none(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "local.toml"

    write_local(path, {"comfy": {"host": "box.local", "port": 8188}})

    assert tomllib.loads(path.read_text(encoding="utf-8"))["comfy"] == {
        "host": "box.local",
        "port": 8188,
    }


def test_write_local_writes_values_that_still_parse(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    path.write_text(LOCAL, encoding="utf-8")

    write_local(path, {"paths": {"output_dir": 'a "quoted" name'}})

    wrote = tomllib.loads(path.read_text(encoding="utf-8"))

    assert wrote["paths"]["output_dir"] == 'a "quoted" name'


def test_write_local_ignores_a_lookalike_key_in_another_section(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    path.write_text(LOCAL, encoding="utf-8")

    write_local(path, {"comfy": {"host": "box.local"}})

    text = path.read_text(encoding="utf-8")
    assert '"box.local"' not in text.split("[paths]")[0].split("[comfy]")[0]
    assert 'img2img = "fish-img2img.json"' in text


# ---------- 地址 ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("127.0.0.1", ("127.0.0.1", 8188)),
        ("127.0.0.1:9000", ("127.0.0.1", 9000)),
        ("http://127.0.0.1:9000/", ("127.0.0.1", 9000)),
        ("https://box.local:1", ("box.local", 1)),
        ("  box.local:12  ", ("box.local", 12)),
        ("box.local", ("box.local", 8188)),
    ],
)
def test_parse_address_takes_the_shapes_we_mean(text: str, expected: tuple[str, int]) -> None:
    assert parse_address(text, 8188) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "127.0.0.1:abc",
        "127.0.0.1:70000",
        "127.0.0.1:0",
        "ftp://127.0.0.1:8188",
        "127.0.0.1:8188/api",
        ":8188",
    ],
)
def test_parse_address_refuses_what_is_not_an_address(text: str) -> None:
    with pytest.raises(ValueError) as caught:
        parse_address(text, 8188)
    assert "——" in str(caught.value)


# ---------- 谁写回、怎么写 ----------


def test_the_repo_offers_its_templates_to_choose_from() -> None:
    choices = workflow_choices(REPO_ROOT)
    assert "fish-txt2img.json" in choices
    assert choices == tuple(sorted(choices))


def test_the_two_theme_values_have_human_labels() -> None:
    assert [value for value, _ in THEME_LABELS] == ["light", "dark"]
    assert all(label for _, label in THEME_LABELS)


def test_set_engine_writes_local_and_rereads(tmp_path: Path) -> None:
    repo = _repo(tmp_path, local=LOCAL)
    before = (repo / "config" / "default.toml").read_bytes()
    settings = Settings(load_config(repo))

    config = settings.set_engine("127.0.0.1:9999")

    assert (config.comfy.host, config.comfy.port) == ("127.0.0.1", 9999)
    assert settings.config.comfy.port == 9999
    assert _local(repo)["comfy"]["port"] == 9999
    assert _local(repo)["comfy"]["host"] == "127.0.0.1"
    assert (repo / "config" / "default.toml").read_bytes() == before


def test_set_engine_without_a_port_keeps_the_one_we_have(tmp_path: Path) -> None:
    repo = _repo(tmp_path, local=LOCAL)

    config = Settings(load_config(repo)).set_engine("box.local")

    assert (config.comfy.host, config.comfy.port) == ("box.local", 8188)


def test_set_workflow_remembers_the_choice(tmp_path: Path) -> None:
    repo = _repo(tmp_path, local=LOCAL)

    config = Settings(load_config(repo)).set_workflow("fish-img2img.json")

    assert config.workflows.default == "fish-img2img.json"
    assert _local(repo)["workflows"]["default"] == "fish-img2img.json"


def test_set_workflow_refuses_a_name_that_is_not_there(tmp_path: Path) -> None:
    repo = _repo(tmp_path, local=LOCAL)
    path = repo / "config" / "local.toml"
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        Settings(load_config(repo)).set_workflow("nope.json")

    assert "nope.json" in str(caught.value)
    assert "fish-txt2img.json" in str(caught.value)
    assert path.read_text(encoding="utf-8") == before


def test_set_paths_cleans_blanks_and_repeats(tmp_path: Path) -> None:
    repo = _repo(tmp_path, local=LOCAL)

    config = Settings(load_config(repo)).set_paths(
        asset_roots=["", "  ", "/tmp/one", "/tmp/one", " /tmp/two "],
        output_dir="  out2  ",
    )

    assert [str(root) for root in config.paths.asset_roots] == ["/tmp/one", "/tmp/two"]
    assert config.paths.output_dir == repo / "out2"
    assert _local(repo)["paths"]["asset_roots"] == ["/tmp/one", "/tmp/two"]


def test_set_paths_refuses_an_empty_output_dir(tmp_path: Path) -> None:
    repo = _repo(tmp_path, local=LOCAL)

    with pytest.raises(ValueError) as caught:
        Settings(load_config(repo)).set_paths(asset_roots=["/tmp/one"], output_dir="   ")

    assert "输出目录" in str(caught.value)


def test_set_theme_writes_the_ui_section(tmp_path: Path) -> None:
    repo = _repo(tmp_path, local=LOCAL)

    config = Settings(load_config(repo)).set_theme("dark")

    assert config.ui.theme == "dark"
    assert _local(repo)["ui"]["theme"] == "dark"
    written = (repo / "config" / "local.toml").read_text(encoding="utf-8")
    assert "# 本机配置 —— 不进 git。" in written


def test_set_theme_refuses_an_unknown_value(tmp_path: Path) -> None:
    repo = _repo(tmp_path, local=LOCAL)

    with pytest.raises(ValueError) as caught:
        Settings(load_config(repo)).set_theme("midnight")

    assert "midnight" in str(caught.value)


def test_a_write_that_cannot_be_read_back_is_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, local=LOCAL)
    path = repo / "config" / "local.toml"
    before = path.read_text(encoding="utf-8")
    settings = Settings(load_config(repo))

    def boom(_root: Path) -> None:
        raise ConfigError("配置读不出来：假的")

    monkeypatch.setattr("lab.settings.load_config", boom)

    with pytest.raises(ConfigError):
        settings.set_theme("dark")

    assert path.read_text(encoding="utf-8") == before


def test_a_write_that_cannot_be_read_back_leaves_no_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    settings = Settings(load_config(repo))

    def boom(_root: Path) -> None:
        raise ConfigError("配置读不出来：假的")

    monkeypatch.setattr("lab.settings.load_config", boom)

    with pytest.raises(ConfigError):
        settings.set_theme("dark")

    assert not (repo / "config" / "local.toml").exists()


def test_write_local_replaces_a_key_that_is_already_there(tmp_path: Path) -> None:
    path = tmp_path / "local.toml"
    path.write_text(LOCAL, encoding="utf-8")

    write_local(path, {"comfy": {"host": "one.local", "port": 8188}})
    write_local(path, {"comfy": {"host": "two.local", "port": 9999}})

    text = path.read_text(encoding="utf-8")
    assert text.count("host = ") == 1
    assert text.count("port = ") == 1
    assert "one.local" not in text
    assert tomllib.loads(text)["comfy"] == {
        "root": "/opt/ComfyUI",
        "host": "two.local",
        "port": 9999,
    }
