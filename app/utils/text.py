def split_message(text: str, limit: int = 4000) -> list[str]:
    """Telegram xabari 4096 belgidan oshmasligi uchun matnni qatorlar bo'yicha bo'ladi."""
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts
