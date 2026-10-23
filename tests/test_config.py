"""配置加载的行为：默认配置 + 本机覆盖（深合并）。

这两个文件的职责在 design.md §5 定死了：
  config/default.toml  进 git，只有与机器无关的默认值
  config/local.toml    不进 git，放本机绝对路径
"""

from pathlib import Path

import pytest

from lab.config import ConfigError, load_config

REPO_ROOT = Path(__file__).resolve().parent.parent

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
steps = 20
cfg = 5.0
width = 1024
height = 1024
denoise = 0.55
prompt_prefix = ""
negative = ""
"""


def _repo(tmp_path: Path, default: str, local: str | None = None) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "default.toml").write_text(default, encoding="utf-8")
    if local is not None:
        (cfg / "local.toml").write_text(local, encoding="utf-8")
    return tmp_path


def test_local_overrides_only_the_keys_it_names(tmp_path):
    repo = _repo(
        tmp_path,
        DEFAULT,
        '[paths]\nasset_roots = ["/somewhere/assets"]\n',
    )

    cfg = load_config(repo)

    assert [str(p) for p in cfg.paths.asset_roots] == ["/somewhere/assets"]
    assert cfg.paths.output_dir.name == "output"  # 段内其它键必须保住
    assert cfg.comfy.port == 8188


def test_local_can_override_a_scalar(tmp_path):
    repo = _repo(tmp_path, DEFAULT, "[comfy]\nport = 9999\n")

    cfg = load_config(repo)

    assert cfg.comfy.port == 9999
    assert cfg.comfy.host == "127.0.0.1"


def test_missing_local_file_is_normal(tmp_path):
    repo = _repo(tmp_path, DEFAULT)

    cfg = load_config(repo)

    assert cfg.comfy.host == "127.0.0.1"
    assert cfg.paths.asset_roots == ()


def test_missing_key_names_itself_in_the_error(tmp_path):
    repo = _repo(tmp_path, '[comfy]\nhost = "127.0.0.1"\n')  # 缺 port

    with pytest.raises(ConfigError) as excinfo:
        load_config(repo)

    assert "comfy.port" in str(excinfo.value)


def test_missing_default_file_is_an_error(tmp_path):
    (tmp_path / "config").mkdir()

    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path)

    assert "default.toml" in str(excinfo.value)


def test_broken_toml_is_reported_as_config_error(tmp_path):
    repo = _repo(tmp_path, "this is not toml = = =")

    with pytest.raises(ConfigError) as excinfo:
        load_config(repo)

    assert "default.toml" in str(excinfo.value)


def test_repo_default_config_loads():
    """仓库里那份真 default.toml 必须读得出来 —— 它就是可用配置键的清单。"""
    cfg = load_config(REPO_ROOT)

    assert cfg.comfy.port == 8188
    assert cfg.comfy.required_nodes
    assert cfg.comfy.weights
    assert cfg.paths.output_dir.is_absolute()
