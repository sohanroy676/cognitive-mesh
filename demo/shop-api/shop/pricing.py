def total(items: list[tuple[str, float, int]]) -> float:
    return sum(price * qty for _, price, qty in items)
