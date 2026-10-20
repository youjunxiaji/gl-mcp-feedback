#!/usr/bin/env python3
"""
Cursor 钩子安装与卸载测试
=========================

`gl-mcp-feedback hooks install / uninstall`：把随包发布的钩子脚本复制到 Cursor 配置目录，
用登记的命令试跑一次，通过后把五项钩子合并登记进 hooks.json，别的钩子原样保留。
"""

import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from mcp_feedback_enhanced import __main__ as cli
from mcp_feedback_enhanced import hook_installer
from mcp_feedback_enhanced.hook_installer import (
    HOOK_SPECS,
    SCRIPT_NAME,
    UV_INSTALL_URL,
    HookInstaller,
)


pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="占位的 uv 是 sh 脚本，只在 macOS / Linux 上跑"
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_SCRIPT = REPO_ROOT / "hooks" / SCRIPT_NAME
# 模块导入时的 PATH 和 uv：下面的 fixture 会把测试期间的 PATH 换成只有占位 uv 的目录，真跑脚本时要用这两个
REAL_PATH = os.environ.get("PATH", "")
REAL_UV = shutil.which("uv")

TIMEOUT = 10
# 用户自己登记的、指向仓库里脚本的位置
OTHER_SCRIPT = "/work/gl-mcp-feedback/hooks/gl-feedback.py"
# 占位的 uv：把「工作目录 参数」记进日志，按钩子的约定输出 {}；PATH 里只有它，所以只能用 shell 内置命令
STUB_UV = """#!/bin/sh
read -r _ || true
printf '%s\\n' "$(pwd -P) $*" >>"$STUB_UV_LOG"
printf '{}\\n'
"""


@pytest.fixture(autouse=True)
def stub_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """PATH 只指向一个放着占位 uv 的目录，试跑和依赖检查的结果不看这台机器；缺 uv 的测试把它删掉"""
    directory = tmp_path / "bin"
    directory.mkdir()
    write_stub_uv(directory, STUB_UV)
    monkeypatch.setenv("PATH", str(directory))
    monkeypatch.setenv("STUB_UV_LOG", str(tmp_path / "uv-calls.log"))
    return directory


def write_stub_uv(directory: Path, body: str) -> None:
    stub = directory / "uv"
    stub.write_text(body)
    stub.chmod(0o755)


def uv_calls(tmp_path: Path) -> list[str]:
    """占位 uv 被调用的记录，没被调用过就是空"""
    log = tmp_path / "uv-calls.log"
    return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def cursor_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "cursor"
    directory.mkdir()
    return directory


def installed_script(directory: Path) -> Path:
    return directory / "hooks" / SCRIPT_NAME


def backup_file(directory: Path) -> Path:
    return directory / "hooks.json.bak"


def our_entries() -> dict[str, list[dict[str, Any]]]:
    """装好之后 hooks 表里属于本项目的五项；写成字面量，和代码里的 HOOK_SPECS 互相对照

    用户级钩子从 Cursor 配置目录运行，所以脚本路径是相对的，命令和装在哪个目录无关。
    """
    launcher = "uv run --quiet --no-project --script ./hooks/gl-feedback.py"
    return {
        "preToolUse": [
            {
                "command": f"{launcher} pre",
                "matcher": ".*interactive_feedback.*",
                "timeout": TIMEOUT,
            }
        ],
        "postToolUse": [{"command": f"{launcher} post", "timeout": TIMEOUT}],
        "postToolUseFailure": [{"command": f"{launcher} post", "timeout": TIMEOUT}],
        "sessionStart": [{"command": f"{launcher} log", "timeout": TIMEOUT}],
        "stop": [{"command": f"{launcher} stop", "timeout": TIMEOUT}],
    }


def registered_elsewhere() -> dict[str, list[dict[str, Any]]]:
    """用户自己登记的五项，指向仓库里的脚本"""
    launcher = f"uv run --quiet --no-project --script {OTHER_SCRIPT}"
    return {
        "preToolUse": [
            {
                "command": f"{launcher} pre",
                "matcher": ".*interactive_feedback.*",
                "timeout": 5,
            }
        ],
        "postToolUse": [{"command": f"{launcher} post", "timeout": 5}],
        "postToolUseFailure": [{"command": f"{launcher} post", "timeout": 5}],
        "sessionStart": [{"command": f"{launcher} log", "timeout": 5}],
        "stop": [{"command": f"{launcher} stop", "timeout": 5}],
    }


def other_hooks() -> dict[str, list[dict[str, Any]]]:
    """别人的钩子，装和卸都不能碰"""
    return {
        "afterFileEdit": [{"command": "./format.sh"}],
        "stop": [{"command": "./notify.sh", "timeout": 30}],
        "preToolUse": [{"command": "./audit.sh", "matcher": "Shell"}],
    }


def write_config(directory: Path, config: object) -> str:
    """写一份 hooks.json，返回写下的文本"""
    text = config if isinstance(config, str) else json.dumps(config, indent=4) + "\n"
    (directory / "hooks.json").write_text(text, encoding="utf-8")
    return text


def read_config_text(directory: Path) -> str:
    return (directory / "hooks.json").read_text(encoding="utf-8")


def read_config(directory: Path) -> dict[str, Any]:
    return json.loads(read_config_text(directory))


class TestInstall:
    def test_fresh_install_copies_the_script_and_registers_every_hook(
        self, cursor_dir: Path
    ) -> None:
        report = HookInstaller(cursor_dir).install()

        assert report.ok
        assert installed_script(cursor_dir).read_bytes() == HOOK_SCRIPT.read_bytes()
        assert read_config(cursor_dir) == {
            "version": 1,
            "hooks": our_entries(),
        }
        assert not backup_file(cursor_dir).exists()
        assert "✔ 试跑通过" in report.lines
        assert not any(line.startswith("⚠") for line in report.lines)

    def test_creates_the_cursor_directory_when_it_is_missing(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "fresh" / ".cursor"

        assert HookInstaller(missing).install().ok

        assert read_config(missing)["hooks"] == our_entries()
        assert installed_script(missing).is_file()

    def test_rerun_changes_nothing(self, cursor_dir: Path) -> None:
        HookInstaller(cursor_dir).install()
        before = read_config_text(cursor_dir)

        report = HookInstaller(cursor_dir).install()

        assert report.ok
        assert read_config_text(cursor_dir) == before
        assert not backup_file(cursor_dir).exists()
        assert sum("已登记，未改动" in line for line in report.lines) == len(HOOK_SPECS)
        assert any("脚本已是最新" in line for line in report.lines)

    def test_adds_the_missing_version_to_an_existing_config(
        self, cursor_dir: Path
    ) -> None:
        write_config(cursor_dir, {"hooks": other_hooks()})

        report = HookInstaller(cursor_dir).install()

        assert report.ok
        config = read_config(cursor_dir)
        assert config["version"] == 1
        assert config["hooks"]["afterFileEdit"] == other_hooks()["afterFileEdit"]

    def test_keeps_other_hooks_and_backs_up_the_original(
        self, cursor_dir: Path
    ) -> None:
        original = write_config(
            cursor_dir,
            {"version": 1, "extra": {"keep": True}, "hooks": other_hooks()},
        )

        report = HookInstaller(cursor_dir).install()

        assert report.ok
        config = read_config(cursor_dir)
        ours = our_entries()
        others = other_hooks()
        assert config["extra"] == {"keep": True}
        assert config["hooks"]["afterFileEdit"] == others["afterFileEdit"]
        assert config["hooks"]["stop"] == [*others["stop"], *ours["stop"]]
        assert config["hooks"]["preToolUse"] == [
            *others["preToolUse"],
            *ours["preToolUse"],
        ]
        for event in ("postToolUse", "postToolUseFailure", "sessionStart"):
            assert config["hooks"][event] == ours[event]
        assert backup_file(cursor_dir).read_text(encoding="utf-8") == original

    def test_existing_registrations_are_left_alone(
        self, cursor_dir: Path, tmp_path: Path
    ) -> None:
        original = write_config(
            cursor_dir, {"version": 1, "hooks": registered_elsewhere()}
        )

        report = HookInstaller(cursor_dir).install()

        assert report.ok
        assert read_config_text(cursor_dir) == original
        assert not backup_file(cursor_dir).exists()
        assert not installed_script(cursor_dir).exists()
        assert uv_calls(tmp_path) == []
        assert sum("已登记，未改动" in line for line in report.lines) == len(HOOK_SPECS)

    def test_registers_only_the_missing_events(self, cursor_dir: Path) -> None:
        elsewhere = registered_elsewhere()
        write_config(
            cursor_dir,
            {"version": 1, "hooks": {"postToolUse": elsewhere["postToolUse"]}},
        )

        HookInstaller(cursor_dir).install()

        hooks = read_config(cursor_dir)["hooks"]
        ours = our_entries()
        assert hooks["postToolUse"] == elsewhere["postToolUse"]
        for event in ("preToolUse", "postToolUseFailure", "sessionStart", "stop"):
            assert hooks[event] == ours[event]
        assert installed_script(cursor_dir).is_file()

    def test_force_points_every_registration_at_the_installed_script(
        self, cursor_dir: Path
    ) -> None:
        elsewhere = registered_elsewhere()
        duplicate = {"command": elsewhere["stop"][0]["command"], "timeout": 99}
        hooks = {
            **elsewhere,
            "stop": [{"command": "./notify.sh"}, *elsewhere["stop"], duplicate],
        }
        write_config(cursor_dir, {"version": 1, "hooks": hooks})

        report = HookInstaller(cursor_dir).install(force=True)

        assert report.ok
        ours = our_entries()
        new_hooks = read_config(cursor_dir)["hooks"]
        # 第一条匹配的原地换成新位置，重复的删掉，别人的钩子不动
        assert new_hooks["stop"] == [{"command": "./notify.sh"}, *ours["stop"]]
        for event in (
            "preToolUse",
            "postToolUse",
            "postToolUseFailure",
            "sessionStart",
        ):
            assert new_hooks[event] == ours[event]
        assert installed_script(cursor_dir).is_file()

    def test_rerun_refreshes_the_installed_script(self, cursor_dir: Path) -> None:
        installer = HookInstaller(cursor_dir)
        installer.install()
        script = installed_script(cursor_dir)
        script.write_text("# 旧版本\n", encoding="utf-8")
        config_before = read_config_text(cursor_dir)

        report = installer.install()

        assert report.ok
        assert script.read_bytes() == HOOK_SCRIPT.read_bytes()
        assert any("脚本已更新" in line for line in report.lines)
        assert read_config_text(cursor_dir) == config_before
        assert not backup_file(cursor_dir).exists()

    @pytest.mark.parametrize(
        "content",
        ["{not json", "[]", '{"hooks": []}', '{"hooks": {"stop": "./notify.sh"}}'],
        ids=["invalid-json", "top-level-array", "hooks-not-object", "event-not-array"],
    )
    def test_refuses_a_hooks_json_it_cannot_merge_into(
        self, cursor_dir: Path, tmp_path: Path, content: str
    ) -> None:
        original = write_config(cursor_dir, content)

        report = HookInstaller(cursor_dir).install()

        assert not report.ok
        assert read_config_text(cursor_dir) == original
        assert not backup_file(cursor_dir).exists()
        assert not installed_script(cursor_dir).exists()
        assert uv_calls(tmp_path) == []

    def test_reports_a_script_it_cannot_find(
        self, cursor_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def not_found() -> bytes:
            raise FileNotFoundError("找不到钩子脚本")

        monkeypatch.setattr(
            HookInstaller, "read_packaged_script", staticmethod(not_found)
        )

        report = HookInstaller(cursor_dir).install()

        assert not report.ok
        assert list(cursor_dir.iterdir()) == []

    def test_a_directory_with_spaces_needs_no_quoting(self, tmp_path: Path) -> None:
        spaced = tmp_path / "my cursor"
        installer = HookInstaller(spaced)
        installer.install()
        written = read_config_text(spaced)

        assert read_config(spaced)["hooks"] == our_entries()
        # 命令里的路径是相对的，目录名里的空格不碍事；再装一次什么都不改
        installer.install()
        assert read_config_text(spaced) == written

    def test_commands_are_plain_enough_for_every_shell(self, cursor_dir: Path) -> None:
        HookInstaller(cursor_dir).install()

        commands = [
            entry["command"]
            for entries in read_config(cursor_dir)["hooks"].values()
            for entry in entries
        ]

        # 没有引号、变量、反斜杠和空格以外的特殊字符：bash、zsh、PowerShell 里的意思一样
        assert len(commands) == len(HOOK_SPECS)
        assert all(re.fullmatch(r"[A-Za-z0-9 ./_-]+", command) for command in commands)
        assert all(command.startswith("uv ") for command in commands)

    def test_defaults_to_the_cursor_directory_in_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))

        report = HookInstaller().install()

        assert report.ok
        assert (tmp_path / ".cursor" / "hooks" / SCRIPT_NAME).is_file()
        assert any("~/.cursor/hooks/gl-feedback.py" in line for line in report.lines)


class TestTrialRun:
    def test_runs_the_registered_command_from_the_cursor_directory(
        self, cursor_dir: Path, tmp_path: Path
    ) -> None:
        HookInstaller(cursor_dir).install()
        command = our_entries()["postToolUse"][0]["command"]

        # Cursor 跑用户级钩子时工作目录是配置目录，试跑照着来
        assert uv_calls(tmp_path) == [
            f"{cursor_dir.resolve()} {' '.join(shlex.split(command)[1:])}"
        ]

    @pytest.mark.parametrize(
        ("behavior", "detail"),
        [
            ("echo 'boom: no python' >&2\nexit 2\n", "退出码 2）：boom: no python"),
            ("read -r _ || true\necho hello\n", "退出码 0）：hello"),
            ("read -r _ || true\n", "没有输出"),
        ],
        ids=["exits-with-2", "prints-garbage", "prints-nothing"],
    )
    def test_a_failing_trial_registers_nothing(
        self, cursor_dir: Path, stub_uv: Path, behavior: str, detail: str
    ) -> None:
        write_stub_uv(stub_uv, f"#!/bin/sh\n{behavior}")

        report = HookInstaller(cursor_dir).install()

        assert not report.ok
        assert any(detail in line for line in report.lines)
        assert any("没有改动 hooks.json" in line for line in report.lines)
        # 什么都没留下：没有 hooks.json，复制出来的脚本和空目录也撤掉
        assert list(cursor_dir.iterdir()) == []

    def test_a_failing_trial_leaves_an_existing_install_alone(
        self, cursor_dir: Path, stub_uv: Path
    ) -> None:
        HookInstaller(cursor_dir).install()
        config_before = read_config_text(cursor_dir)
        write_stub_uv(stub_uv, "#!/bin/sh\nexit 2\n")

        report = HookInstaller(cursor_dir).install(force=True)

        assert not report.ok
        assert read_config_text(cursor_dir) == config_before
        assert not backup_file(cursor_dir).exists()
        assert installed_script(cursor_dir).is_file()

    def test_a_failing_trial_keeps_other_hooks_untouched(
        self, cursor_dir: Path, stub_uv: Path
    ) -> None:
        original = write_config(cursor_dir, {"version": 1, "hooks": other_hooks()})
        write_stub_uv(stub_uv, "#!/bin/sh\nexit 2\n")

        report = HookInstaller(cursor_dir).install()

        assert not report.ok
        assert read_config_text(cursor_dir) == original
        assert not backup_file(cursor_dir).exists()
        assert not installed_script(cursor_dir).exists()

    @pytest.mark.parametrize(
        "error",
        [
            subprocess.TimeoutExpired("uv", 60),
            OSError("Exec format error"),
        ],
        ids=["timeout", "cannot-start"],
    )
    def test_a_trial_that_cannot_finish_is_a_failure(
        self,
        cursor_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
    ) -> None:
        def run(*_args: object, **_kwargs: object) -> None:
            raise error

        monkeypatch.setattr(hook_installer.subprocess, "run", run)

        report = HookInstaller(cursor_dir).install()

        assert not report.ok
        assert any("试跑钩子脚本失败" in line for line in report.lines)
        assert list(cursor_dir.iterdir()) == []

    def test_without_uv_the_trial_is_skipped_and_the_install_warns(
        self, cursor_dir: Path, stub_uv: Path
    ) -> None:
        (stub_uv / "uv").unlink()

        report = HookInstaller(cursor_dir).install()

        assert report.ok
        assert read_config(cursor_dir)["hooks"] == our_entries()
        assert "✔ 试跑通过" not in report.lines
        warning = next(line for line in report.lines if line.startswith("⚠"))
        assert "uv" in warning
        assert UV_INSTALL_URL in warning

    @pytest.mark.skipif(REAL_UV is None, reason="需要 uv")
    def test_registered_command_runs_through_the_real_uv(
        self, cursor_dir: Path, tmp_path: Path
    ) -> None:
        HookInstaller(cursor_dir).install()
        command = read_config(cursor_dir)["hooks"]["postToolUse"][0]["command"]
        assert REAL_UV is not None

        # 和 Cursor 一样：配置目录里，uv 加登记的参数
        result = subprocess.run(
            [REAL_UV, *shlex.split(command)[1:]],
            cwd=cursor_dir,
            input=json.dumps({"conversation_id": "a1b2c3d4"}),
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": REAL_PATH,
                "GL_FEEDBACK_HOME": str(tmp_path / "home"),
            },
            timeout=60,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {}


class TestPackagedScript:
    def test_falls_back_to_the_source_checkout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            hook_installer.resources, "files", lambda _name: tmp_path / "empty-package"
        )

        assert HookInstaller.read_packaged_script() == HOOK_SCRIPT.read_bytes()

    def test_prefers_the_copy_inside_the_package(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        package = tmp_path / "package"
        (package / "hooks").mkdir(parents=True)
        (package / "hooks" / SCRIPT_NAME).write_bytes(b"# packaged\n")
        monkeypatch.setattr(hook_installer.resources, "files", lambda _name: package)

        assert HookInstaller.read_packaged_script() == b"# packaged\n"

    def test_wheel_ships_the_script_where_the_installer_looks(self) -> None:
        pyproject = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"][
            "force-include"
        ]

        assert force_include == {
            f"hooks/{SCRIPT_NAME}": f"mcp_feedback_enhanced/hooks/{SCRIPT_NAME}"
        }
        assert (REPO_ROOT / "hooks" / SCRIPT_NAME).is_file()

    def test_hook_specs_match_the_modes_the_script_serves(self) -> None:
        script = HOOK_SCRIPT.read_text(encoding="utf-8")
        handlers = re.search(
            r"^HANDLERS = \{(.*?)\}$", script, flags=re.MULTILINE | re.DOTALL
        )
        assert handlers is not None
        script_modes = set(re.findall(r'"(\w+)": run_\w+', handlers.group(1)))

        assert script_modes == {spec.mode for spec in HOOK_SPECS}
        for mode in script_modes:
            command = f"uv run --script /x/{SCRIPT_NAME} {mode}"
            assert HookInstaller.entry_mode({"command": command}) == mode


class TestEntryMode:
    @pytest.mark.parametrize(
        ("command", "mode"),
        [
            ("uv run --quiet --no-project --script ./hooks/gl-feedback.py pre", "pre"),
            ("uv run --script /Users/me/.cursor/hooks/gl-feedback.py post", "post"),
            ("uv run --script '/Users/my cursor/hooks/gl-feedback.py' stop", "stop"),
            ('uv run --script "/a b/gl-feedback.py" log', "log"),
            (r"uv run --script C:\Users\me\.cursor\hooks\gl-feedback.py pre", "pre"),
            (r"uv run --script 'C:\Users\my cursor\hooks\gl-feedback.py' stop", "stop"),
            ("python3 /x/gl-feedback.py log  ", "log"),
            ("gl-feedback.py pre", "pre"),
            ("/x/gl-feedback.py", None),
            ("/x/gl-feedback.py pre extra", None),
            ("/x/gl-feedback.py other", None),
            ("./my-gl-feedback.py pre", None),
            ("./hooks/gl-feedback.py.bak pre", None),
            ("./notify.sh stop", None),
        ],
    )
    def test_recognizes_our_commands(self, command: str, mode: str | None) -> None:
        assert HookInstaller.entry_mode({"command": command}) == mode

    @pytest.mark.parametrize(
        "entry",
        ["gl-feedback.py pre", None, 7, {"matcher": "x"}, {"command": 5}, ["command"]],
    )
    def test_ignores_entries_that_are_not_commands(self, entry: object) -> None:
        assert HookInstaller.entry_mode(entry) is None


class TestUninstall:
    def test_removes_our_hooks_and_script_but_keeps_the_rest(
        self, cursor_dir: Path
    ) -> None:
        write_config(cursor_dir, {"version": 1, "hooks": other_hooks()})
        installer = HookInstaller(cursor_dir)
        installer.install()
        installed = read_config_text(cursor_dir)

        report = installer.uninstall()

        assert report.ok
        assert read_config(cursor_dir) == {"version": 1, "hooks": other_hooks()}
        assert not installed_script(cursor_dir).exists()
        assert backup_file(cursor_dir).read_text(encoding="utf-8") == installed

    def test_removes_the_hooks_folder_only_when_it_is_empty(
        self, cursor_dir: Path
    ) -> None:
        installer = HookInstaller(cursor_dir)
        installer.install()

        installer.uninstall()

        assert not (cursor_dir / "hooks").exists()

    def test_keeps_the_hooks_folder_when_other_scripts_live_there(
        self, cursor_dir: Path
    ) -> None:
        installer = HookInstaller(cursor_dir)
        installer.install()
        other = cursor_dir / "hooks" / "format.sh"
        other.write_text("#!/bin/bash\n", encoding="utf-8")

        installer.uninstall()

        assert other.exists()
        assert not installed_script(cursor_dir).exists()

    def test_removes_registrations_that_point_elsewhere(self, cursor_dir: Path) -> None:
        elsewhere = registered_elsewhere()
        others = other_hooks()
        hooks = {
            **elsewhere,
            "afterFileEdit": others["afterFileEdit"],
            "stop": [*others["stop"], *elsewhere["stop"]],
            "preToolUse": [*others["preToolUse"], *elsewhere["preToolUse"]],
        }
        write_config(cursor_dir, {"version": 1, "hooks": hooks})

        report = HookInstaller(cursor_dir).uninstall()

        assert report.ok
        # 只剩别人的钩子；空了的事件整个删掉，不留空数组
        assert read_config(cursor_dir)["hooks"] == others

    def test_without_a_hooks_json_only_the_script_goes(self, cursor_dir: Path) -> None:
        script = installed_script(cursor_dir)
        script.parent.mkdir(parents=True)
        script.write_text("# 脚本\n", encoding="utf-8")

        report = HookInstaller(cursor_dir).uninstall()

        assert report.ok
        assert not script.exists()
        assert not (cursor_dir / "hooks.json").exists()
        assert not backup_file(cursor_dir).exists()

    def test_nothing_to_remove_leaves_every_file_untouched(
        self, cursor_dir: Path
    ) -> None:
        original = write_config(cursor_dir, {"version": 1, "hooks": other_hooks()})

        report = HookInstaller(cursor_dir).uninstall()

        assert report.ok
        assert read_config_text(cursor_dir) == original
        assert not backup_file(cursor_dir).exists()

    def test_refuses_a_broken_hooks_json_and_keeps_the_script(
        self, cursor_dir: Path
    ) -> None:
        script = installed_script(cursor_dir)
        script.parent.mkdir(parents=True)
        script.write_text("# 脚本\n", encoding="utf-8")
        original = write_config(cursor_dir, "{not json")

        report = HookInstaller(cursor_dir).uninstall()

        assert not report.ok
        assert read_config_text(cursor_dir) == original
        assert script.exists()

    def test_does_not_touch_commands_that_only_look_similar(
        self, cursor_dir: Path
    ) -> None:
        hooks = {"stop": [{"command": "./my-gl-feedback.py stop"}]}
        original = write_config(cursor_dir, {"version": 1, "hooks": hooks})

        HookInstaller(cursor_dir).uninstall()

        assert read_config_text(cursor_dir) == original


class TestCommandLine:
    def run_cli(self, monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
        monkeypatch.setattr(sys, "argv", ["gl-mcp-feedback", *args])
        with pytest.raises(SystemExit) as exit_info:
            cli.main()
        code = exit_info.value.code
        assert isinstance(code, int)
        return code

    def test_install_then_uninstall(
        self,
        cursor_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        code = self.run_cli(
            monkeypatch, "hooks", "install", "--cursor-dir", str(cursor_dir)
        )

        assert code == 0
        assert read_config(cursor_dir)["hooks"] == our_entries()
        assert "preToolUse 已登记" in capsys.readouterr().out

        code = self.run_cli(
            monkeypatch, "hooks", "uninstall", "--cursor-dir", str(cursor_dir)
        )

        assert code == 0
        assert read_config(cursor_dir)["hooks"] == {}
        assert not installed_script(cursor_dir).exists()

    def test_force_flag_is_passed_on(
        self, cursor_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_config(cursor_dir, {"version": 1, "hooks": registered_elsewhere()})

        code = self.run_cli(
            monkeypatch,
            "hooks",
            "install",
            "--force",
            "--cursor-dir",
            str(cursor_dir),
        )

        assert code == 0
        assert read_config(cursor_dir)["hooks"] == our_entries()

    def test_a_failed_install_exits_with_1(
        self,
        cursor_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        write_config(cursor_dir, "{not json")

        code = self.run_cli(
            monkeypatch, "hooks", "install", "--cursor-dir", str(cursor_dir)
        )

        assert code == 1
        assert "✖" in capsys.readouterr().out

    def test_survives_a_stdout_that_cannot_encode_the_symbols(
        self, cursor_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="ascii")
        monkeypatch.setattr(sys, "stdout", stream)

        code = self.run_cli(
            monkeypatch, "hooks", "install", "--cursor-dir", str(cursor_dir)
        )
        stream.flush()

        assert code == 0
        assert b"preToolUse" in buffer.getvalue()

    def test_hooks_without_a_subcommand_is_a_usage_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self.run_cli(monkeypatch, "hooks") == 2

    def test_defaults_to_the_cursor_directory_in_home(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))

        code = self.run_cli(monkeypatch, "hooks", "install")

        assert code == 0
        assert (tmp_path / ".cursor" / "hooks" / SCRIPT_NAME).is_file()
        assert "~/.cursor/hooks/gl-feedback.py" in capsys.readouterr().out
