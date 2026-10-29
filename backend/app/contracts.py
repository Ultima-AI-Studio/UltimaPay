# -*- coding: utf-8 -*-
"""
contracts —— C1-C6 公共契约（UGP-06 统一时间、金额、标识、状态、错误响应与响应形状）

设计：纯模块（import 无副作用）；所有可注入点（时钟）以 `now_fn` 参数暴露，
默认使用 datetime.now()（与原 feed_server 行为一致：北京时间字符串 '%Y-%m-%d %H:%M:%S'）。
"""
from datetime import datetime, timedelta

# ── C1 时间契约 ──
TIME_FORMAT = '%Y-%m-%d %H:%M:%S'
DATE_FORMAT = '%Y-%m-%d'


def now_iso(now_fn=None):
    """统一时间戳（%Y-%m-%d %H:%M:%S）。now_fn 可注入测试时钟，缺省 datetime.now()。"""
    dt = now_fn() if now_fn else datetime.now()
    return dt.strftime(TIME_FORMAT)


def today_str(now_fn=None):
    """统一日期（%Y-%m-%d）。"""
    dt = now_fn() if now_fn else datetime.now()
    return dt.strftime(DATE_FORMAT)


def add_days_iso(days, base=None, fmt=TIME_FORMAT):
    """在 base（字符串）上加 days 天，返回格式化字符串；base 为 None 从当前时间起算。"""
    dt = datetime.strptime(base, fmt) if base else datetime.now()
    return (dt + timedelta(days=days)).strftime(fmt)


# ── C2 金额契约（USDT 数字，两位小数）──
AMOUNT_EPS = 0.005  # 对账容差：|实付 - 订单| 超过该值判不匹配


def fmt_amount(x):
    """金额归一：None/非法 → None；否则 round 到 2 位。"""
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


def amount_eq(a, b, eps=AMOUNT_EPS):
    """金额等值比较（防浮点误差）。"""
    try:
        return abs(float(a) - float(b)) <= eps
    except (TypeError, ValueError):
        return False


# ── C3 标识契约（生成规则登记；各域保持原格式）──
ID_RULES = {
    'user_id': "U + YYYYMMDDHHMMSS + 3 位随机数字（auth.register 生成）",
    'order_no': "PAY + YYYYMMDDHHMMSS + 4 位随机数字（payments.create_payment_order 生成）",
    'invite_code': "6 位大写字母+数字（membership.get_invite_code 生成，每邀请人固定一个）",
    'merchant_code': "MC + 大写字母数字（接入方申请时生成）",
}


# ── C4 状态契约 ──
PAYMENT_STATUS = {
    'PENDING': 'pending',      # 已下单，等待到账
    'SUBMITTED': 'submitted',  # 收到回执，等待核验（UGP-03：回执永不自动 confirmed）
    'CONFIRMED': 'confirmed',  # 链上核验通过（onchain 流程实现前不写入）
    'EXPIRED': 'expired',      # 超时未到账失效
    'FAILED': 'failed',        # 失败/反向
}
ORDER_STATUS = ('paid', 'expiring', 'reversed')       # 平台订单表 orders.status
USER_STATUS = ('active', 'disabled')                  # users.status
PERMISSION_LEVELS = ('standard', 'advanced')          # user_product_perm.permission


# ── C5 错误响应契约（P0 X-02 收敛：唯一主形状 {code, msg, data}）──
# 止血收敛：管理/业务接口统一 code 形。成功 {code:0, msg:'ok', data:...}；
# 失败 {code:<非0>, msg:'错误信息', data:null}。前端拦截器按 code 判断并弹 res.msg。
ERR_OK = 0


def ok_data(data):
    """{code: 0, msg:'ok', data: ...} —— 唯一主形状（成功）。"""
    return {'code': ERR_OK, 'msg': 'ok', 'data': data}


def err(status, message):
    """{code: <status>, msg: ..., data:null} —— 唯一主形状（失败）。"""
    return {'code': status, 'msg': message, 'data': None}


def ok_legacy(data):
    """DEPRECATED（过渡兼容，待退役）：{ok: True, data: ...}。仅 legacy_adapter 兼容层保留。"""
    return {'ok': True, 'data': data}


def err_legacy(message):
    """DEPRECATED（过渡兼容，待退役）：{ok: False, error: ...}。仅 legacy_adapter 兼容层保留。"""
    return {'ok': False, 'error': message}


# ── C6 响应形状适配（入口层统一出口）──
# P0 X-02：双形状收敛为单一主形状 'code' {code, msg, data}。
# 旧 {ok,data} 形状不再作为管理接口输出；ok_legacy/err_legacy 仅作 legacy_adapter 过渡兼容，待退役。
RESPONSE_SHAPES = ('code',)


def shape_of(obj):
    """识别一个响应体形状：'code' / 'legacy_ok'(过渡) / 'unknown'。"""
    if isinstance(obj, dict):
        if 'ok' in obj:
            return 'legacy_ok'
        if 'code' in obj:
            return 'code'
    return 'unknown'

