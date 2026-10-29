# -*- coding: utf-8 -*-
"""
payments/money —— UGP-16 金额精度（整数最小单位，纯标准库，无浮点）

- 金额内部一律使用整数最小单位（链 decimals 决定的定点单位），不经过浮点。
- 十进制字符串 ↔ 最小单位整数：编码→解码恒等；不可精确表示（小数位超过 decimals、
  负数、非数字）→ ValueError 明确拒绝，绝不停默截断。
- 金融报表 6 位口径与链最小单位分开：报表展示用 report_decimal（定点、显式口径），
  与链上最小单位各自独立换算，不混用。
"""
from decimal import Decimal, InvalidOperation, ROUND_DOWN

REPORT_DIGITS = 6   # 金融报表口径：6 位小数（UGP-16 登记）


def parse_amount_units(amount_decimal, decimals):
    """十进制字符串 → 最小单位整数（int）。
    不可精确表示（小数位 > decimals / 负数 / 非数字）→ ValueError（拒绝而非截断）。"""
    decimals = int(decimals)
    if decimals < 0 or decimals > 30:
        raise ValueError('decimals 超出支持范围: %r' % (decimals,))
    try:
        d = Decimal(str(amount_decimal).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('金额不是合法十进制数: %r' % (amount_decimal,))
    if not d.is_finite():
        raise ValueError('金额必须是有穷数: %r' % (amount_decimal,))
    if d < 0:
        raise ValueError('金额不能为负: %r' % (amount_decimal,))
    # 小数位检查：超过 decimals 表示无法用该链最小单位精确表示
    exp = -d.as_tuple().exponent
    if exp > decimals:
        raise ValueError('金额 %s 精度超过链最小单位（%d 位），拒绝而非截断' % (amount_decimal, decimals))
    units = int(d.scaleb(decimals).to_integral_value())
    # 反查恒等：换算回十进制数值必须与输入一致（Decimal 数值比较，防任何舍入路径）
    if Decimal(format_amount_units(units, decimals)) != d:
        raise ValueError('金额 %s 无法精确表示为 %d 位最小单位' % (amount_decimal, decimals))
    return units


def format_amount_units(units, decimals):
    """最小单位整数 → 十进制字符串（定点、无浮点；保留到链精度位数）。
    例：units=1234500, decimals=6 → '1.234500'。"""
    units = int(units)
    decimals = int(decimals)
    if units < 0:
        raise ValueError('最小单位不能为负: %r' % (units,))
    sign = ''
    body = str(units)
    if decimals <= 0:
        return body
    if len(body) <= decimals:
        body = '0' * (decimals - len(body) + 1) + body
    return sign + body[:-decimals] + '.' + body[-decimals:]


def amount_to_report(units, decimals, report_digits=REPORT_DIGITS):
    """链最小单位 → 报表 6 位口径（定点四舍五入到 report_digits，明确为展示口径）。
    与链最小单位分开：同一金额两种口径各自独立，报表不反写链上精度。"""
    dec = Decimal(format_amount_units(units, decimals))
    q = Decimal(1).scaleb(-int(report_digits))
    return str(dec.quantize(q, rounding=ROUND_DOWN))


def roundtrip_ok(amount_decimal, decimals):
    """编码→解码恒等校验（测试与运行时自检用）。"""
    try:
        u = parse_amount_units(amount_decimal, decimals)
    except ValueError:
        return False
    return Decimal(format_amount_units(u, decimals)) == Decimal(str(amount_decimal))

