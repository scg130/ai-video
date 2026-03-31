"""调试：控制台打印模型类调用的输入与输出（全文可能很长，仅建议开发环境使用）。"""

_SEP = "=" * 72


def print_chat_model_io(label: str, system: str, user: str, output: str) -> None:
    print(
        f"\n{_SEP}\n[模型 I/O] {label}\n--- system ---\n{system}\n"
        f"--- user ---\n{user}\n--- assistant(原始返回) ---\n{output}\n{_SEP}\n",
        flush=True,
    )


def print_model_io(label: str, input_block: str, output_block: str) -> None:
    print(
        f"\n{_SEP}\n[模型 I/O] {label}\n--- 输入 ---\n{input_block}\n"
        f"--- 输出 ---\n{output_block}\n{_SEP}\n",
        flush=True,
    )
