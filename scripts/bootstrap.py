#!/usr/bin/env python3
"""Choose a usable Python runtime and install only missing skill dependencies."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_ENV = "VIDEO_TRANSCRIBE_CONFIG"


# Optional local-VAD deps (groq hallucination guard + silence splitting).
# Best-effort: when absent or uninstallable the pipeline degrades to the
# word-gap heuristics instead of failing.
_OPTIONAL_VAD = [("silero_vad_notorch", "silero-vad-notorch"),
                 ("onnxruntime", "onnxruntime")]


def requirements_for(engine: str, system: str | None = None, machine: str | None = None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    requirements = [("jieba", "jieba==0.42.1"), ("numpy", "numpy")]
    if engine == "groq":
        requirements.append(("requests", "requests"))
    elif engine == "local":
        if system == "Darwin" and machine in {"arm64", "aarch64"}:
            requirements.extend([
                ("mlx_whisper", "mlx-whisper"),
                ("soundfile", "soundfile"),
            ])
        else:
            requirements.append(("faster_whisper", "faster-whisper"))
    return requirements


def _package_missing(python: str, module: str, requirement: str) -> bool:
    probe = "import importlib.util, importlib.metadata, sys; " \
            "sys.exit(importlib.util.find_spec(sys.argv[1]) is None or " \
            "(len(sys.argv) > 3 and importlib.metadata.version(sys.argv[2]) != sys.argv[3]))"
    distribution, separator, version = requirement.partition("==")
    args = [python, "-c", probe, module]
    if separator:
        args.extend([distribution, version])
    result = subprocess.run(args, capture_output=True)
    return result.returncode != 0


def _runtime_python(env_dir: Path, windows: bool | None = None) -> Path:
    windows = os.name == "nt" if windows is None else windows
    return env_dir / ("Scripts/python.exe" if windows else "bin/python3")


def _can_run(python: Path) -> bool:
    try:
        return subprocess.run(
            [str(python), "-c", "import sys; sys.exit(sys.version_info < (3, 10))"],
            capture_output=True,
            timeout=10,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _make_venv(python: str, env_dir: Path) -> Path:
    runtime = _runtime_python(env_dir)
    if env_dir.exists():
        if _can_run(runtime):
            return runtime
        alternate = SKILL_DIR / ".venv"
        alternate_runtime = _runtime_python(alternate)
        if alternate.exists() and _can_run(alternate_runtime):
            return alternate_runtime
        # A moved or upgraded base Python can leave the venv launcher broken.
        # --upgrade relinks it without deleting the existing site-packages.
        try:
            subprocess.run([python, "-m", "venv", "--upgrade", str(env_dir)], check=True)
        except subprocess.CalledProcessError:
            pass
        if _can_run(runtime):
            return runtime
        # Preserve an unrecoverable old venv and fall back to a fresh sibling.
        env_dir = SKILL_DIR / ".venv"
        runtime = _runtime_python(env_dir)
        if _can_run(runtime):
            return runtime

    subprocess.run([python, "-m", "venv", str(env_dir)], check=True)
    if not _can_run(runtime):
        raise RuntimeError(f"创建的虚拟环境无法启动：{runtime}")
    return runtime


def _resolve_engine(requested: str, config_path: str | None = None) -> str:
    if requested != "auto":
        return requested

    config_path = config_path or os.environ.get(CONFIG_ENV)
    if not config_path:
        raise RuntimeError(
            "未指定引擎。请传入 --engine groq/local，或设置 VIDEO_TRANSCRIBE_CONFIG。"
        )

    try:
        config = json.loads(Path(config_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"无法读取 {CONFIG_ENV} 指定的配置文件：{exc}") from exc

    engine = config.get("engine") if isinstance(config, dict) else None
    if engine not in {"groq", "local"}:
        raise RuntimeError(f"{CONFIG_ENV} 配置中的 engine 必须是 groq 或 local。")
    return engine


def prepare(engine: str) -> Path:
    if sys.version_info < (3, 10):
        raise RuntimeError("需要 Python 3.10 或更新版本。请升级 Python 后重试。")

    # 技能自包含：一律使用技能目录下的虚拟环境，不依赖也不污染系统
    # Python 的 site-packages。首次运行时创建并装齐依赖，之后复用。
    requirements = requirements_for(engine)
    env_dir = SKILL_DIR / "venv"
    runtime = _make_venv(sys.executable, env_dir)
    missing = [package for module, package in requirements
               if _package_missing(str(runtime), module, package)]
    if missing:
        subprocess.run([str(runtime), "-m", "ensurepip", "--upgrade"], check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run([str(runtime), "-m", "pip", "install", *missing], check=True)

    missing_modules = [module for module, package in requirements
                       if _package_missing(str(runtime), module, package)]
    if missing_modules:
        raise RuntimeError("安装后仍无法导入：" + ", ".join(missing_modules))

    for module, package in _OPTIONAL_VAD:
        if _package_missing(str(runtime), module, package):
            installed = subprocess.run([str(runtime), "-m", "pip", "install", package],
                                       capture_output=True, text=True)
            if installed.returncode != 0 or _package_missing(str(runtime), module, package):
                # 常见原因：执行 Python 过新（如 3.15），onnxruntime 尚无
                # 匹配 wheel。打印 pip 错误摘要方便诊断，降级不中断。
                err = (installed.stderr or "").strip().splitlines()
                reason = next((l for l in err if "Could not find" in l or "No matching" in l), "")
                print(f"提示：{package} 安装失败{('：' + reason) if reason else ''}，"
                      "长音频 VAD 幻觉防护将降级为词间隙启发式。"
                      "若为 Python 版本过新所致，可用 3.10-3.13 重建技能 venv。",
                      file=sys.stderr)
    return runtime


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("auto", "groq", "local"), default="auto")
    args = parser.parse_args()
    try:
        engine = _resolve_engine(args.engine)
        runtime = prepare(engine)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"依赖初始化失败：{exc}", file=sys.stderr)
        print("请使用 Python 3.10+，并确认网络可访问 PyPI。", file=sys.stderr)
        return 1
    print(f"engine={engine}")
    print(f"Python executable: {runtime}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
