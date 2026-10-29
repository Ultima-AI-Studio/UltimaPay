"""Payment product catalog backed by the independent UltimaPay database."""

from .compat import LEGACY_PRODUCT_REF


SUBSCRIPTION_PLANS = [
    {"id": "SP-01", "product": LEGACY_PRODUCT_REF, "name": "月卡", "priceUsdt": 198,
     "priceCny": None, "durationDays": 30, "giftDays": 0, "discount": 1.0,
     "enabled": True, "remark": "标准月卡，无赠送", "createdAt": "2026-09-01"},
    {"id": "SP-02", "product": LEGACY_PRODUCT_REF, "name": "季卡", "priceUsdt": 458,
     "priceCny": None, "durationDays": 90, "giftDays": 5, "discount": 0.8,
     "enabled": True, "remark": "定价 458，约 8 折，赠 5 天", "createdAt": "2026-09-01"},
    {"id": "SP-03", "product": LEGACY_PRODUCT_REF, "name": "年卡", "priceUsdt": 1198,
     "priceCny": None, "durationDays": 365, "giftDays": 15, "discount": 0.6,
     "enabled": True, "remark": "定价 1198，约 6 折，赠 15 天", "createdAt": "2026-09-10"},
    {"id": "SP-04", "product": LEGACY_PRODUCT_REF, "name": "体验卡", "priceUsdt": 0,
     "priceCny": None, "durationDays": 7, "giftDays": 0, "discount": 1.0,
     "enabled": True, "remark": "免费体验 7 天", "createdAt": "2026-09-20"},
]


def read_subscription_plans(db, product=None):
    rows = db.execute(
        "SELECT id, name, product, price_usdt, price_cny, duration_days, gift_days, discount,"
        " enabled, remark, created_at FROM subscription_plans ORDER BY id"
    ).fetchall()
    plans = [
        {"id": row[0], "name": row[1], "product": row[2], "priceUsdt": row[3],
         "priceCny": row[4], "durationDays": row[5], "giftDays": row[6],
         "discount": row[7], "enabled": bool(row[8]), "remark": row[9] or "",
         "createdAt": row[10]}
        for row in rows
    ] or [dict(plan) for plan in SUBSCRIPTION_PLANS]
    if product:
        plans = [plan for plan in plans if plan["product"] == product]
    return plans

