#!/usr/bin/env python3
"""
Graphical setup wizard (launcher) for end users of UUMC Generator.

Pure Python stdlib (tkinter): scans Steam libraries for Unity games, checks
Mono/BepInEx 5 compatibility via `diagnose_environment`, creates a mod workspace
with configurable local bug reporting, and shows clear next steps for any AI IDE.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

try:
    from cli.bootstrap import (
        bootstrap_workspace,
        diagnose_environment,
        discover_steam_games,
    )
    from core.indexer.profile import resolve_uumc_root
    from core.testing.mcp_bridge import build_mcp_config_snippet
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cli.bootstrap import (
        bootstrap_workspace,
        diagnose_environment,
        discover_steam_games,
    )
    from core.indexer.profile import resolve_uumc_root
    from core.testing.mcp_bridge import build_mcp_config_snippet


def suggest_project_defaults(game_dir: Path, game_name: str | None = None) -> tuple[Path, str]:
    """Computes default mod project folder and C# mod name next to the game folder."""
    resolved = game_dir.expanduser().resolve()
    raw_name = (game_name or resolved.name).strip()
    cleaned = "".join(ch for ch in raw_name if ch.isalnum() or ch == "_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = "MyUnityGame"
    mod_name = cleaned if cleaned.endswith("Mod") else f"{cleaned}Mod"
    target_dir = resolved.parent / mod_name
    return target_dir, mod_name


def summarize_diagnosis_for_ui(report: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Converts diagnose_environment output into concise UI status and detail lines."""
    verdict = str(report.get("verdict") or "UNKNOWN")
    if verdict == "READY":
        status_key = "ready"
        headline = "Игра подходит — можно создавать проект мода"
    elif verdict == "NEEDS_SETUP":
        status_key = "setup"
        headline = "Игра на Unity Mono (поддерживается), но нужна подготовка среды"
    else:
        status_key = "unsupported"
        headline = "Эту игру нельзя модифицировать в текущей версии UUMC"

    details: list[str] = []
    for chk in report.get("checks") or ():
        st = chk.get("status", "")
        mark = {"ok": "[OK]", "warn": "[Внимание]", "fail": "[Нужно]", "unsupported": "[Стоп]"}.get(st, "[Info]")
        details.append(f"{mark} {chk.get('summary', '')}")
        if chk.get("fix_hint"):
            details.append(f"      -> {chk['fix_hint']}")
    return status_key, headline, details


def can_enable_full_setup(report: dict[str, Any]) -> tuple[bool, str | None]:
    """Checks whether full setup (--index + --deploy-devmcp) prerequisites are met."""
    checks = {str(c.get("name")): str(c.get("status")) for c in (report.get("checks") or ()) if isinstance(c, dict)}
    if checks.get("unity_runtime") == "unsupported":
        return False, "Игра не поддерживается текущим стеком UUMC."
    if checks.get("bepinex5") != "ok":
        return False, "Для полной настройки сначала установите BepInEx 5 в папку игры."
    if checks.get("dotnet_sdk") != "ok":
        return False, "Для индексации и сборки требуется .NET SDK 8.0+."
    return True, None


def _open_folder(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def run_launcher(uumc_root: Path | None = None) -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError:
        print(
            "[ERROR] Модуль tkinter недоступен в текущей сборке Python. "
            "Используйте консольный мастер: python cli/uumc.py init --wizard",
            file=sys.stderr,
        )
        return 1

    root_dir = (uumc_root or resolve_uumc_root()).resolve()

    try:
        app = tk.Tk()
    except tk.TclError as exc:
        print(
            f"[ERROR] Не удалось открыть графическое окно ({exc}). "
            "Используйте консольный мастер: python cli/uumc.py init --wizard",
            file=sys.stderr,
        )
        return 1

    app.title("UUMC — Генератор проектов модов для Unity")
    app.geometry("760x640")
    app.minsize(680, 560)

    discovered_games: list[dict[str, Any]] = []
    created_workspace: Path | None = None

    game_dir_var = tk.StringVar()
    steam_choice_var = tk.StringVar()
    target_dir_var = tk.StringVar()
    mod_name_var = tk.StringVar()
    full_setup_var = tk.BooleanVar(value=True)
    report_local_var = tk.BooleanVar(value=False)
    status_var = tk.StringVar(value="Выберите папку с игрой или нажмите «Найти игры в Steam».")

    main_frame = ttk.Frame(app, padding=14)
    main_frame.pack(fill="both", expand=True)

    ttk.Label(
        main_frame,
        text="UUMC — Подготовка проекта мода для Unity (Mono + BepInEx 5)",
        font=("Segoe UI", 12, "bold"),
    ).pack(anchor="w", pady=(0, 8))

    # --- Step 1: Game selection & compatibility check ---
    step1 = ttk.LabelFrame(main_frame, text="1. Выбор и проверка игры", padding=10)
    step1.pack(fill="x", pady=(0, 8))

    steam_row = ttk.Frame(step1)
    steam_row.pack(fill="x", pady=(0, 6))
    steam_combo = ttk.Combobox(steam_row, textvariable=steam_choice_var, state="readonly")
    steam_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))

    def on_scan_steam() -> None:
        nonlocal discovered_games
        discovered_games = discover_steam_games()
        if not discovered_games:
            steam_combo["values"] = []
            messagebox.showinfo("Поиск игр", "Игры Unity в стандартных библиотеках Steam не найдены. Укажите папку вручную.")
            return
        labels = []
        for item in discovered_games:
            tag = "Mono ✓" if item["supported"] else "IL2CPP ✗"
            labels.append(f"[{tag}] {item['name']} — {item['game_dir']}")
        steam_combo["values"] = labels
        steam_combo.current(0)
        on_select_steam()

    def on_select_steam(_event: object = None) -> None:
        idx = steam_combo.current()
        if 0 <= idx < len(discovered_games):
            chosen = discovered_games[idx]
            game_dir_var.set(chosen["game_dir"])
            on_check_game(auto_fill=True)

    steam_combo.bind("<<ComboboxSelected>>", on_select_steam)
    ttk.Button(steam_row, text="Найти игры в Steam", command=on_scan_steam).pack(side="left")

    path_row = ttk.Frame(step1)
    path_row.pack(fill="x", pady=(0, 6))
    ttk.Label(path_row, text="Папка игры:").pack(side="left", padx=(0, 6))
    ttk.Entry(path_row, textvariable=game_dir_var).pack(side="left", fill="x", expand=True, padx=(0, 6))

    def on_browse_game() -> None:
        chosen = filedialog.askdirectory(title="Выберите папку с игрой (рядом с .exe)")
        if chosen:
            game_dir_var.set(chosen)
            on_check_game(auto_fill=True)

    ttk.Button(path_row, text="Обзор...", command=on_browse_game).pack(side="left", padx=(0, 6))

    status_label = ttk.Label(step1, textvariable=status_var, font=("Segoe UI", 9, "bold"))
    status_label.pack(anchor="w", pady=(2, 4))

    diag_box = tk.Text(step1, height=7, wrap="word", state="disabled", font=("Consolas", 9))
    diag_box.pack(fill="x")

    def _set_diag_text(lines: list[str]) -> None:
        diag_box.configure(state="normal")
        diag_box.delete("1.0", "end")
        diag_box.insert("1.0", "\n".join(lines))
        diag_box.configure(state="disabled")

    def on_check_game(auto_fill: bool = False) -> dict[str, Any] | None:
        raw = game_dir_var.get().strip()
        if not raw:
            messagebox.showwarning("Проверка игры", "Сначала выберите папку с игрой.")
            return None
        gdir = Path(raw)
        if not gdir.is_dir():
            status_var.set("Папка игры не найдена.")
            _set_diag_text([f"Указанный путь не существует: {gdir}"])
            return None

        report = diagnose_environment(explicit_game_dir=gdir, uumc_root=root_dir)
        status_key, headline, details = summarize_diagnosis_for_ui(report)
        color = {"ready": "#1b7f3b", "setup": "#9a6700", "unsupported": "#b42318"}.get(status_key, "#222222")
        status_label.configure(foreground=color)
        status_var.set(headline)
        _set_diag_text(details)

        if auto_fill or not target_dir_var.get().strip():
            gname = (report.get("game") or {}).get("game_name")
            def_target, def_mod = suggest_project_defaults(gdir, gname)
            target_dir_var.set(str(def_target))
            mod_name_var.set(def_mod)

        can_create = status_key != "unsupported"
        create_btn.configure(state="normal" if can_create else "disabled")
        full_ok, _ = can_enable_full_setup(report)
        if full_ok:
            full_setup_chk.configure(state="normal")
            full_setup_var.set(True)
        else:
            full_setup_var.set(False)
            full_setup_chk.configure(state="disabled")
        return report

    ttk.Button(path_row, text="Проверить", command=lambda: on_check_game(auto_fill=True)).pack(side="left")

    # --- Step 2: Mod workspace creation ---
    step2 = ttk.LabelFrame(main_frame, text="2. Создание проекта мода (Верстак)", padding=10)
    step2.pack(fill="x", pady=(0, 8))

    target_row = ttk.Frame(step2)
    target_row.pack(fill="x", pady=(0, 6))
    ttk.Label(target_row, text="Папка проекта:", width=15).pack(side="left")
    ttk.Entry(target_row, textvariable=target_dir_var).pack(side="left", fill="x", expand=True, padx=(0, 6))

    def on_browse_target() -> None:
        chosen = filedialog.askdirectory(title="Куда сохранить новый проект мода")
        if chosen:
            target_dir_var.set(chosen)

    ttk.Button(target_row, text="Обзор...", command=on_browse_target).pack(side="left")

    name_row = ttk.Frame(step2)
    name_row.pack(fill="x", pady=(0, 6))
    ttk.Label(name_row, text="Имя мода (C#):", width=15).pack(side="left")
    ttk.Entry(name_row, textvariable=mod_name_var).pack(side="left", fill="x", expand=True)

    full_setup_chk = ttk.Checkbutton(
        step2,
        text="Полная настройка (проиндексировать сборку игры и установить Uumc.DevMcp)",
        variable=full_setup_var,
    )
    full_setup_chk.pack(anchor="w")
    ttk.Checkbutton(
        step2,
        text="Сохранять локальные баг-репорты инструментов UUMC в папку reports/ проекта",
        variable=report_local_var,
    ).pack(anchor="w", pady=(2, 6))

    create_btn = ttk.Button(step2, text="Создать проект мода")
    create_btn.pack(anchor="w")

    # --- Step 3: Handoff to AI IDE ---
    step3 = ttk.LabelFrame(main_frame, text="3. Что делать дальше", padding=10)
    step3.pack(fill="both", expand=True)

    next_box = tk.Text(step3, height=6, wrap="word", state="disabled", font=("Segoe UI", 9))
    next_box.pack(fill="both", expand=True, pady=(0, 8))

    def _set_next_text(text: str) -> None:
        next_box.configure(state="normal")
        next_box.delete("1.0", "end")
        next_box.insert("1.0", text)
        next_box.configure(state="disabled")

    _set_next_text(
        "После создания проекта:\n"
        "1. Откройте созданную папку проекта в вашей IDE или программе с ИИ-агентом.\n"
        "2. При желании скопируйте конфиг MCP в mcp_config.json для живой отладки игры.\n"
        "3. Напишите агенту: «Помоги начать первый мод»."
    )

    actions_row = ttk.Frame(step3)
    actions_row.pack(fill="x")

    open_btn = ttk.Button(
        actions_row,
        text="Открыть папку проекта",
        state="disabled",
        command=lambda: _open_folder(created_workspace) if created_workspace else None,
    )
    open_btn.pack(side="left", padx=(0, 8))

    def on_copy_mcp() -> None:
        if created_workspace is None:
            return
        snippet = build_mcp_config_snippet(workspace_dir=created_workspace)
        app.clipboard_clear()
        app.clipboard_append(json.dumps(snippet, indent=2, ensure_ascii=False))
        messagebox.showinfo("MCP-конфиг", "JSON-блок для mcp_config.json скопирован в буфер обмена.")

    mcp_btn = ttk.Button(actions_row, text="Скопировать конфиг MCP", state="disabled", command=on_copy_mcp)
    mcp_btn.pack(side="left")

    def on_create_project() -> None:
        nonlocal created_workspace
        raw_game = game_dir_var.get().strip()
        raw_target = target_dir_var.get().strip()
        raw_mod = mod_name_var.get().strip()
        if not raw_game or not raw_target:
            messagebox.showwarning("Создание проекта", "Укажите папку с игрой и папку для нового проекта.")
            return

        report = on_check_game(auto_fill=False)
        if report is None or report.get("verdict") == "UNSUPPORTED":
            messagebox.showerror("Не поддерживается", "Эту игру нельзя модифицировать в текущей версии UUMC.")
            return

        full = bool(full_setup_var.get())
        rep_mode = "local" if report_local_var.get() else "off"
        create_btn.configure(state="disabled")
        _set_next_text("Создание проекта мода и настройка среды... Пожалуйста, подождите.")

        def worker() -> None:
            try:
                res = bootstrap_workspace(
                    Path(raw_game),
                    Path(raw_target),
                    mod_name=raw_mod or None,
                    run_index=full,
                    deploy_devmcp=full,
                    with_enricher=full,
                    report_mode=rep_mode,
                    uumc_root=root_dir,
                )
            except Exception as exc:  # noqa: BLE001
                err_text = str(exc)

                def on_fail() -> None:
                    create_btn.configure(state="normal")
                    _set_next_text(f"Ошибка создания проекта:\n{err_text}")
                    messagebox.showerror("Ошибка", err_text)

                app.after(0, on_fail)
                return

            def on_ok() -> None:
                nonlocal created_workspace
                created_workspace = Path(str(res["target"]))
                create_btn.configure(state="normal")
                open_btn.configure(state="normal")
                mcp_btn.configure(state="normal")
                _set_next_text(
                    f"Готово! Проект мода создан: {created_workspace}\n\n"
                    "Дальнейшие шаги:\n"
                    f"1. Откройте папку «{created_workspace}» в вашей IDE с ИИ-агентом.\n"
                    "2. Нажмите «Скопировать конфиг MCP» и добавьте его в mcp_config.json (если нужна живая отладка).\n"
                    "3. Напишите агенту в новом окне: «Помоги начать первый мод»."
                )

            app.after(0, on_ok)

        threading.Thread(target=worker, daemon=True).start()

    create_btn.configure(command=on_create_project)
    app.mainloop()
    return 0


def main() -> None:
    sys.exit(run_launcher())


if __name__ == "__main__":
    main()
