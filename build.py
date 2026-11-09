#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create MC GUI 资源包打包脚本

用法：
    python build.py -v 1.0.0 -m 2      # 自用版：只打包到 dist/
    python build.py -v 1.0.0 -m 1      # 发行版：打包 + git tag/push + gh release

不传参数时会进入交互式提问。

设计约束：
    * 白名单打包：只收 assets/、LICENSE（或 license）、pack.mcmeta、pack.png
    * pack.mcmeta 原样写进 zip，绝不改动其中任何字节（打包前后都会校验 SHA-256）
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
ASSETS_DIR = ROOT / "assets"
PACK_MC_META = ROOT / "pack.mcmeta"
PACK_PNG = ROOT / "pack.png"
LICENSE_NAMES = (
    "LICENSE",
    "LICENSE.txt",
    "LICENSE.md",
    "license",
    "license.txt",
    "license.md",
    "License",
)

MODE_NAMES = {1: "发行版", 2: "自用版"}

# 输出重定向（管道/日志）时统一用 UTF-8，避免中文乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def out(msg=""):
    print(msg, flush=True)


def die(msg, code=1):
    print()
    print("[错误] " + msg)
    print()
    sys.exit(code)


def warn(msg):
    print("[警告] " + msg)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def human_size(num):
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return "%.1f %s" % (num, unit)
        num /= 1024.0


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #
def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="build.py",
        description="Create MC GUI 资源包打包脚本（白名单打包到 dist/）",
        add_help=True,
    )
    parser.add_argument("-v", "--version", dest="version", help="版本号，例如 1.0.0（不要带 v）")
    parser.add_argument(
        "-m",
        "--mode",
        dest="mode",
        help="打包模式：1 = 发行版（打包 + tag + push + GitHub Release），2 = 自用版（仅本地打包）",
    )
    return parser.parse_args(argv)


def normalize_version(raw):
    v = (raw or "").strip()
    if v[:1] in ("v", "V"):
        v = v[1:].strip()
    return v


def ask_version():
    out("=" * 60)
    out(" Create MC GUI 资源包打包")
    out("=" * 60)
    out("")
    while True:
        raw = input("请输入版本号（例如 1.0.0，不要带 v）：").strip()
        version = normalize_version(raw)
        if version:
            return version
        out("[提示] 版本号不能为空，请重新输入。")


def parse_mode(raw):
    s = (raw or "").strip()
    if not s:
        return None
    head = s[0]
    if head in ("1", "2"):
        return int(head)
    return None


def ask_mode():
    out("")
    out("请选择打包模式：")
    out("  1. 发行版（打包 + git tag/push + GitHub Release）")
    out("  2. 自用版（仅本地打包到 dist/）")
    while True:
        mode = parse_mode(input("输入 1 或 2："))
        if mode in MODE_NAMES:
            return mode
        out("[提示] 只能输入 1 或 2，请重新选择。")


# --------------------------------------------------------------------------- #
# 收集文件（白名单）
# --------------------------------------------------------------------------- #
def find_license():
    for name in LICENSE_NAMES:
        p = ROOT / name
        if p.is_file():
            return p
    return None


def collect_files():
    missing = []

    if not PACK_MC_META.is_file():
        missing.append("pack.mcmeta")
    if not ASSETS_DIR.is_dir():
        missing.append("assets/（文件夹）")

    if missing:
        die(
            "以下必要文件/文件夹缺失，无法打包：\n"
            + "\n".join("        - " + m for m in missing)
            + "\n      请确认当前脚本位于资源包根目录（%s）下。" % ROOT
        )

    files = []
    for p in sorted(ASSETS_DIR.rglob("*")):
        if p.is_file():
            files.append(p)
    if not files:
        die("assets/ 目录存在但里面没有任何文件，打包没有意义，请检查资源包内容。")

    files.append(PACK_MC_META)

    if PACK_PNG.is_file():
        files.append(PACK_PNG)
    else:
        warn("未找到 pack.png，资源包在游戏内将显示默认图标（继续打包）。")

    license_path = find_license()
    if license_path is not None:
        files.append(license_path)
    else:
        warn("未找到 LICENSE / license 文件，将不包含许可证（继续打包）。")

    return files


# --------------------------------------------------------------------------- #
# 打包
# --------------------------------------------------------------------------- #
def build_zip(version):
    files = collect_files()

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_name = "%s_v%s.zip" % (ROOT.name, version)
    zip_path = DIST_DIR / zip_name

    meta_hash_before = sha256_of(PACK_MC_META)

    out("")
    out("[1/2] 正在打包 %d 个文件 -> dist/%s" % (len(files), zip_name))
    if zip_path.exists():
        out("      目标文件已存在，将被覆盖。")
        zip_path.unlink()

    try:
        with zipfile.ZipFile(
            zip_path,
            "w",
            zipfile.ZIP_DEFLATED,
            compresslevel=9,
            # 部分文件（如从 zip 解压、mtime 为 1601/1970）早于 ZIP 支持的 1980 年，
            # 允许其被自动钳制到 1980-01-01，否则 zipfile 会直接抛异常。
            strict_timestamps=False,
        ) as zf:
            for p in files:
                arcname = p.relative_to(ROOT).as_posix()
                # 直接写入原始字节，pack.mcmeta 不做任何加工
                zf.write(p, arcname)
    except OSError as exc:
        die("写入 zip 失败：%s\n      请检查 dist/ 是否被其他程序占用。" % exc)

    # 校验 pack.mcmeta 未被修改
    meta_hash_after = sha256_of(PACK_MC_META)
    if meta_hash_before != meta_hash_after:
        zip_path.unlink(missing_ok=True)
        die("pack.mcmeta 在打包过程中发生了变化，已删除本次产物，请检查磁盘/杀毒软件干扰。")

    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad is not None:
            zip_path.unlink(missing_ok=True)
            die("生成的 zip 校验失败（损坏文件：%s），已删除本次产物。" % bad)
        entry_count = len(zf.namelist())

    out("      完成：%d 个条目，%s" % (entry_count, human_size(zip_path.stat().st_size)))
    out("      pack.mcmeta SHA-256：%s（打包前后一致，未被修改）" % meta_hash_after)
    out("      输出路径：%s" % zip_path)
    return zip_path


def open_folder(path):
    """用系统默认文件管理器打开目录（仅 Windows 生效），失败不影响打包结果。"""
    if os.name != "nt":
        return
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]  仅 Windows 提供
    except OSError as exc:
        warn("自动打开 dist/ 失败（%s），请手动打开：%s" % (exc, path))


# --------------------------------------------------------------------------- #
# 发行版流程
# --------------------------------------------------------------------------- #
def run_cmd(cmd, capture=False, allow_fail=False):
    out("      $ " + " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            text=True,
            capture_output=capture,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        die("找不到命令 `%s`，请先安装并确保它在 PATH 中。" % cmd[0])
    if proc.returncode != 0 and not allow_fail:
        detail = ""
        if capture:
            detail = (proc.stderr or proc.stdout or "").strip()
        die(
            "命令执行失败（退出码 %d）：%s\n%s"
            % (proc.returncode, " ".join(cmd), ("      " + detail) if detail else "")
        )
    return proc


def capture(cmd):
    """静默执行命令并捕获输出（强制 UTF-8，避免 gh/git 输出含非 GBK 字符时解码崩溃）。"""
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def preflight_release(version):
    problems = []

    if shutil.which("git") is None:
        problems.append("未找到 git，请先安装 Git 并加入 PATH。")
    if shutil.which("gh") is None:
        problems.append(
            "未找到 GitHub CLI（gh），无法自动创建 Release。\n"
            "        安装：winget install --id GitHub.cli -e\n"
            "        安装后还需执行一次：gh auth login"
        )

    if problems:
        die(
            "发行版（模式 1）前置条件不满足：\n"
            + "\n".join("        - " + p for p in problems)
            + "\n      本次 zip 已生成，但未创建 tag、未 push、未上传 Release。\n"
            "      你也可以手动上传：gh release create v%s \"dist/%s_v%s.zip\""
            % (version, ROOT.name, version)
        )

    inside = capture(["git", "rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        die("当前目录不是 git 仓库，无法创建 tag/push。请确认 %s 下存在 .git。" % ROOT)

    tag = "v" + version
    exists = capture(["git", "rev-parse", "--quiet", "--verify", "refs/tags/" + tag])
    if exists.returncode == 0:
        die("tag %s 已存在。请换一个版本号，或先手动删除该 tag。" % tag)

    auth = capture(["gh", "auth", "status"])
    if auth.returncode != 0:
        die(
            "GitHub CLI 尚未登录，请先在终端执行：gh auth login\n"
            "      （本次 zip 已生成，但未创建 tag、未 push、未上传 Release。）"
        )

    return tag


def release(version, zip_path):
    tag = preflight_release(version)

    out("")
    out("[2/2] 发行版发布流程")
    out("      创建 tag：%s" % tag)
    run_cmd(["git", "tag", "-a", tag, "-m", "Release %s" % tag])

    out("      推送 tag 到 origin ...")
    run_cmd(["git", "push", "origin", tag])

    out("      创建 GitHub Release 并上传 zip ...")
    gh_args = [
        "gh",
        "release",
        "create",
        tag,
        str(zip_path),
        "--title",
        "Create MC GUI %s" % tag,
        "--notes",
        "资源包版本 %s" % tag,
    ]
    # 版本号带连字符（如 1.0.0-beta / 1.0.0-rc.1）视为预发布
    if "-" in version:
        out("      版本号含 '-'，已标记为 pre-release。")
        gh_args.append("--prerelease")
    run_cmd(gh_args)
    out("")
    out("[完成] %s 已发布（%s）。" % (tag, "预发布" if "-" in version else "正式版"))


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if not ROOT.is_dir():
        die("无法定位脚本所在目录。")

    version = normalize_version(args.version)
    mode = parse_mode(args.mode)

    if not version:
        version = ask_version()
    if mode not in MODE_NAMES:
        if (args.mode or "").strip():
            warn("模式参数 %r 无法识别，改为交互式选择。" % args.mode)
        mode = ask_mode()

    out("")
    out("版本号：%s" % version)
    out("模式  ：%d（%s）" % (mode, MODE_NAMES[mode]))
    out("根目录：%s" % ROOT)

    zip_path = build_zip(version)

    if mode == 1:
        release(version, zip_path)
    else:
        out("")
        out("[完成] 自用版已打包：%s" % zip_path)
        out("       该模式不会创建 tag、不会 push、不会上传 Release。")
        out("       正在打开 dist/ 文件夹 ...")
        open_folder(DIST_DIR)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已取消]")
        sys.exit(130)
