#!/usr/bin/env python3
"""
Cursor 钩子的安装与卸载
=======================

供 `gl-mcp-feedback hooks install / uninstall` 使用。

钩子脚本 `gl-feedback.py` 随包发布，由 `uv run --script` 运行。`uvx` 的安装目录是临时缓存路径，不能直接登记，
所以安装时先把脚本复制到 `<Cursor 配置目录>/hooks/`，再把五项钩子合并登记进 `<Cursor 配置目录>/hooks.json`。
用户级钩子从 Cursor 配置目录运行，登记的命令写成 `uv run --quiet --no-project --script ./hooks/gl-feedback.py <模式>`：
所有人的内容一样；命令是 `uv` 加普通参数，没有引号和变量，bash、zsh、PowerShell 都能执行。

登记之前先用同样的命令试跑一次脚本。uv 自己出错时退出码是 2，Cursor 会把 preToolUse 钩子的退出码 2 当成拒绝，
拦住 interactive_feedback 的调用，所以试跑不通就不改 hooks.json。
"""

import json
import re
import shutil
import subprocess
from contextlib import suppress
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, NamedTuple


SCRIPT_NAME = "gl-feedback.py"
HOOK_TIMEOUT_SECONDS = 10
# 试跑钩子脚本的时限：uv 第一次运行要准备环境，机器上没有合适的 Python 时还要下载
TRIAL_TIMEOUT_SECONDS = 60
# 登记的命令里，脚本路径前面的那一段
LAUNCHER = "uv run --quiet --no-project --script"
UV_INSTALL_URL = "https://docs.astral.sh/uv/getting-started/installation/"
# 属于本项目的登记：命令以「gl-feedback.py 模式」结尾，脚本放在哪里、怎么启动都不限；
# 文件名前面必须是目录分隔符（/ 或 \）、引号或空白
COMMAND_PATTERN = re.compile(
    rf"(?:^|[/\\\s'\"]){re.escape(SCRIPT_NAME)}['\"]?\s+(pre|post|log|stop)\s*$"
)


class HookSpec(NamedTuple):
    """一项要登记的钩子：Cursor 的事件名、脚本的模式，`preToolUse` 这类事件还要限定工具的 matcher"""

    event: str
    mode: str
    matcher: str | None = None


HOOK_SPECS = (
    HookSpec("preToolUse", "pre", ".*interactive_feedback.*"),
    HookSpec("postToolUse", "post"),
    HookSpec("postToolUseFailure", "post"),
    HookSpec("sessionStart", "log"),
    HookSpec("stop", "stop"),
)


@dataclass
class HookReport:
    """安装或卸载的结果：要显示给用户的行，以及是否成功"""

    lines: list[str] = field(default_factory=list)
    ok: bool = True

    def add(self, line: str) -> None:
        self.lines.append(line)

    def fail(self, line: str) -> None:
        self.ok = False
        self.lines.append(line)


class HookInstaller:
    """把钩子脚本和钩子登记装进一个 Cursor 配置目录，或从里面卸掉"""

    def __init__(self, cursor_dir: Path | None = None) -> None:
        """
        Args:
            cursor_dir: Cursor 的配置目录，默认是 `~/.cursor`
        """
        self.cursor_dir = cursor_dir or Path.home() / ".cursor"
        self.script_path = self.cursor_dir / "hooks" / SCRIPT_NAME
        self.config_path = self.cursor_dir / "hooks.json"
        self.backup_path = self.cursor_dir / "hooks.json.bak"

    @staticmethod
    def read_packaged_script() -> bytes:
        """读出随包发布的钩子脚本

        安装好的包里，脚本在包目录的 `hooks/` 下；从源码运行时没有这份拷贝，用仓库根目录的 `hooks/`。

        Returns:
            脚本内容

        Raises:
            FileNotFoundError: 两个位置都找不到脚本
        """
        packaged = resources.files("mcp_feedback_enhanced").joinpath(
            "hooks", SCRIPT_NAME
        )
        if packaged.is_file():
            return packaged.read_bytes()
        checkout = Path(__file__).resolve().parents[2] / "hooks" / SCRIPT_NAME
        if checkout.is_file():
            return checkout.read_bytes()
        raise FileNotFoundError(f"找不到钩子脚本 {SCRIPT_NAME}")

    @staticmethod
    def entry_mode(entry: object) -> str | None:
        """判断 hooks.json 里的一条登记是不是本项目的

        Args:
            entry: 事件下的一项登记

        Returns:
            属于本项目时返回它的脚本模式（`pre`、`post`、`log`、`stop`），否则返回 `None`
        """
        if not isinstance(entry, dict):
            return None
        command = entry.get("command")
        match = COMMAND_PATTERN.search(command) if isinstance(command, str) else None
        return match.group(1) if match else None

    def command_for(self, mode: str) -> str:
        """登记在 hooks.json 里的命令

        Args:
            mode: 脚本的模式（`pre`、`post`、`log`、`stop`）

        Returns:
            脚本路径写成相对于 Cursor 配置目录的命令
        """
        relative = self.script_path.relative_to(self.cursor_dir).as_posix()
        return f"{LAUNCHER} ./{relative} {mode}"

    def install(self, force: bool = False) -> HookReport:
        """复制脚本、试跑、登记钩子

        已经登记了 `gl-feedback.py` 的条目（比如开发时指向仓库里的脚本）默认不动。
        没有要登记的条目、脚本也没装过时，不复制脚本，免得留下没有人引用的拷贝。

        Args:
            force: 为真时，这样的条目也改成新位置

        Returns:
            要显示的结果
        """
        report = HookReport()
        try:
            script = self.read_packaged_script()
        except FileNotFoundError as error:
            report.fail(f"✖ {error}")
            return report
        config = self._load_config(report)
        if config is None:
            return report

        registration = self._register_all(config["hooks"], force, report)
        if registration is None:
            return report
        changed, lines = registration
        if changed or self.script_path.exists():
            existed = self.script_path.exists()
            self._copy_script(script, report)
            if not self._trial_run(report):
                if not existed:
                    self.script_path.unlink(missing_ok=True)
                    self._remove_empty_hooks_dir()
                return report
        report.lines.extend(lines)
        if changed:
            self._save_config(config, report)
        if shutil.which("uv") is None:
            report.add(f"⚠ 找不到 uv，钩子跑不起来：安装方法见 {UV_INSTALL_URL}")
        report.add(
            "下一步：在 Cursor 里把 gl-mcp-feedback 这个 MCP 关掉再打开（或重启 Cursor），让新版服务端用上"
        )
        return report

    def uninstall(self) -> HookReport:
        """删掉 hooks.json 里所有 `gl-feedback.py` 的登记（路径不限），再删掉安装位置的脚本

        别的钩子原样保留。

        Returns:
            要显示的结果
        """
        report = HookReport()
        if self.config_path.exists():
            config = self._load_config(report)
            if config is None:
                return report
            if self._unregister_all(config["hooks"], report):
                self._save_config(config, report)
            else:
                report.add(f"• hooks.json 里没有 {SCRIPT_NAME} 的登记")
        else:
            report.add("• 没有 hooks.json，没有登记可删")
        if self.script_path.exists():
            self.script_path.unlink()
            report.add(f"✔ 已删除脚本 {self._display(self.script_path)}")
            self._remove_empty_hooks_dir()
        return report

    def _register_all(
        self, hooks: dict[str, Any], force: bool, report: HookReport
    ) -> tuple[bool, list[str]] | None:
        """把五项钩子登记进 hooks 表

        Returns:
            （有没有改动，要显示的行）；hooks.json 的格式不对时报告失败并返回 `None`
        """
        lines: list[str] = []
        changed = False
        for spec in HOOK_SPECS:
            entries = hooks.setdefault(spec.event, [])
            if not isinstance(entries, list):
                report.fail(
                    f"✖ hooks.json 里的 {spec.event} 不是数组，没有改动任何东西"
                )
                return None
            matching = [
                index
                for index, entry in enumerate(entries)
                if self.entry_mode(entry) == spec.mode
            ]
            if matching and not force:
                lines.append(
                    f"• {spec.event} 已登记，未改动：{entries[matching[0]]['command']}"
                )
                continue
            new_entry = self._build_entry(spec)
            if matching:
                entries[matching[0]] = new_entry
                for index in reversed(matching[1:]):
                    del entries[index]
            else:
                entries.append(new_entry)
            changed = True
            lines.append(f"✔ {spec.event} 已登记")
        return changed, lines

    def _unregister_all(self, hooks: dict[str, Any], report: HookReport) -> bool:
        """从 hooks 表里删掉本项目的登记，空了的事件一并删掉，返回有没有改动"""
        changed = False
        for event in list(hooks):
            entries = hooks[event]
            if not isinstance(entries, list):
                continue
            kept = [entry for entry in entries if self.entry_mode(entry) is None]
            removed = len(entries) - len(kept)
            if not removed:
                continue
            changed = True
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
            report.add(f"✔ 已删除 {event} 的 {removed} 条登记")
        return changed

    def _build_entry(self, spec: HookSpec) -> dict[str, Any]:
        entry: dict[str, Any] = {"command": self.command_for(spec.mode)}
        if spec.matcher is not None:
            entry["matcher"] = spec.matcher
        entry["timeout"] = HOOK_TIMEOUT_SECONDS
        return entry

    def _copy_script(self, script: bytes, report: HookReport) -> None:
        shown = self._display(self.script_path)
        existed = self.script_path.exists()
        if existed and self.script_path.read_bytes() == script:
            report.add(f"• 脚本已是最新 {shown}")
            return
        self.script_path.parent.mkdir(parents=True, exist_ok=True)
        self.script_path.write_bytes(script)
        report.add(f"✔ 脚本已{'更新' if existed else '复制到'} {shown}")

    def _remove_empty_hooks_dir(self) -> None:
        """脚本目录里没有别的文件了就一并删掉，还有别的钩子脚本时保留"""
        with suppress(OSError):
            self.script_path.parent.rmdir()

    def _trial_run(self, report: HookReport) -> bool:
        """用登记的命令试跑一次脚本，确认 uv、Python 和脚本都能用

        还没有 uv 时跳过，由安装结尾的提示说明。

        Returns:
            可以登记时为真；试跑失败时报告失败并返回假
        """
        uv = shutil.which("uv")
        if uv is None:
            return True
        command = [uv, *self.command_for("post").split()[1:]]
        try:
            result = subprocess.run(
                command,
                input=b"{}",
                capture_output=True,
                cwd=self.cursor_dir,
                timeout=TRIAL_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            report.fail(f"✖ 试跑钩子脚本失败：{error}，没有改动 hooks.json")
            return False
        if result.returncode == 0 and result.stdout.strip() == b"{}":
            report.add("✔ 试跑通过")
            return True
        output = (result.stderr or result.stdout).decode(errors="replace").strip()
        detail = output.splitlines()[-1] if output else "没有输出"
        report.fail(
            f"✖ 试跑钩子脚本失败（退出码 {result.returncode}）：{detail}，没有改动 hooks.json"
        )
        return False

    def _load_config(self, report: HookReport) -> dict[str, Any] | None:
        """读 hooks.json；文件不存在时给一份空的，读不了或格式不对时报告并返回 `None`"""
        if not self.config_path.exists():
            return {"version": 1, "hooks": {}}
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            report.fail(
                f"✖ 读不了 {self._display(self.config_path)}：{error}，没有改动任何东西"
            )
            return None
        if not isinstance(data, dict) or not isinstance(
            data.setdefault("hooks", {}), dict
        ):
            report.fail(
                f"✖ {self._display(self.config_path)} 的格式不对（顶层和 hooks 都要是对象），没有改动任何东西"
            )
            return None
        data.setdefault("version", 1)
        return data

    def _save_config(self, config: dict[str, Any], report: HookReport) -> None:
        """写 hooks.json；原文件先备份一份"""
        if self.config_path.exists():
            shutil.copy2(self.config_path, self.backup_path)
            report.add(f"✔ 原 hooks.json 已备份为 {self.backup_path.name}")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(config, indent=4, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        report.add(f"✔ 已写入 {self._display(self.config_path)}")

    @staticmethod
    def _display(path: Path) -> str:
        try:
            return f"~/{path.relative_to(Path.home()).as_posix()}"
        except ValueError:
            return str(path)
