"""Amounts in text: USD minor units, written the way the web shows them (`lib/money.ts`)."""


def usd(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}USD {abs(minor) // 100}.{abs(minor) % 100:02d}"
