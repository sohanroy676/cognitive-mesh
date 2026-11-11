"""The project's own check: `python check_shop.py` (exit 0 = pass). No pytest needed."""

from shop.cart import Cart


def main() -> None:
    cart = Cart()
    cart.add("pen", 2.0, 3)
    cart.add("book", 10.0)
    if cart.total() != 16.0 or Cart().total() != 0:
        raise SystemExit(f"wrong total: {cart.total()}")
    print("shop-api check passed")


if __name__ == "__main__":
    main()
