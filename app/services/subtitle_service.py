"""字幕：将分镜脚本转为 SRT（兼容 time 为 0-5 / 0-5s）"""


def time_range_to_srt_time(time_str: str) -> str:
    """把 "0-5s" / "0-5" 转为 SRT 时间轴"""
    try:
        raw = (time_str or "").strip().lower().replace("s", "")
        parts = raw.split("-")
        if len(parts) != 2:
            return "00:00:00,000 --> 00:00:05,000"
        start_s = int(parts[0].strip())
        end_s = int(parts[1].strip())

        def sec_to_srt(s: int) -> str:
            h, rest = divmod(s, 3600)
            m, sec = divmod(rest, 60)
            return f"{h:02d}:{m:02d}:{sec:02d},000"

        return f"{sec_to_srt(start_s)} --> {sec_to_srt(end_s)}"
    except (ValueError, IndexError):
        return "00:00:00,000 --> 00:00:05,000"


def to_srt(scenes: list[dict]) -> str:
    """分镜列表 → SRT（仅对白）。"""
    srt = []
    for i, s in enumerate(scenes):
        time_str = s.get("time", f"{i*5}-{(i+1)*5}s")
        dialogue = s.get("dialogue", "").strip() or "(画面)"
        srt_time = time_range_to_srt_time(str(time_str))
        srt.append(f"{i + 1}\n{srt_time}\n{dialogue}\n")
    return "\n".join(srt)
