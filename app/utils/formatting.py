def fmt_num(value: float | None) -> str:
    """5.0 -> '5', 2.5 -> '2.5', None -> '0'."""
    if value is None:
        return "0"
    return str(int(value)) if float(value).is_integer() else f"{value:g}"
