from shop.pricing import total


class Cart:
    def __init__(self) -> None:
        self.items: list[tuple[str, float, int]] = []

    def add(self, name: str, price: float, qty: int = 1) -> None:
        self.items.append((name, price, qty))

    def total(self) -> float:
        return total(self.items)
