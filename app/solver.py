"""最小汉明重量故障向量求解器（折半综合征索引 / meet-in-the-middle）。

问题：给定 n 个候选通道与 m 条奇偶校验（每条引用若干通道并观测到一个奇偶值），
求 x ∈ {0,1}^n 使 Hx = s (mod 2) 同时成立且汉明重量最小；同重时按
"通道标识升序形成的选择向量"字典序最小裁决（即在排序最靠前的分歧通道上取 0）。

方法（按需求约束实现）：
  1. 通道按标识自然升序排序后折半为左/右两段；
  2. 左半枚举所有子集，计算其综合征（m 位整数），按综合征建立索引，
     每个综合征只保留 (重量, 字典序键) 最优的候选；
  3. 右半枚举所有子集，所需左半综合征 = s XOR 右半综合征，精确查表合并，
     全局按 (总重量, 左字典序键, 右字典序键) 取最优。

不枚举完整故障向量、不使用随机搜索、不以高斯消元的任意解替代最优结论；
若两侧无法合并出任何候选，则精确判定不可行（而非返回近似集合）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN = re.compile(r"(\d+)")


def natural_key(cid: str):
    """通道标识的自然升序键：数字段按数值比较，其余段按字典序比较。"""
    return tuple((0, int(t)) if t.isdigit() else (1, t) for t in _TOKEN.split(cid))


@dataclass
class SolveResult:
    feasible: bool
    weight: int | None               # 最小汉明重量
    selection: list[int] | None      # 与升序通道对齐的 0/1 选择向量
    fault_channels: list[str] | None # 升序故障通道


def _bit_reverse_table(width: int) -> list[int]:
    """rev[m]：将 m 的低 width 位按位反转。

    比较 rev 整数的大小，等价于从 bit0（即排序最靠前的通道）起比较
    选择向量的字典序，因此可作为字典序键参与整数比较。
    """
    size = 1 << width
    rev = [0] * size
    for m in range(1, size):
        rev[m] = (rev[m >> 1] >> 1) | ((m & 1) << (width - 1))
    return rev


def _syndromes(cols: list[int]) -> list[int]:
    """DP 计算半侧列集合全部子集的综合征（只枚举半侧，不枚举完整故障向量）。"""
    size = 1 << len(cols)
    syn = [0] * size
    for m in range(1, size):
        lsb = m & (-m)
        syn[m] = syn[m ^ lsb] ^ cols[lsb.bit_length() - 1]
    return syn


def solve_min_weight(channels: list[str], checks: list[dict]) -> SolveResult:
    """求解最小汉明重量故障向量。

    channels: 唯一通道标识列表（2–36 个）。
    checks:   [{"channels": [...], "parity": 0|1}, ...]（1–28 条，集合非空且不重复）。
    """
    order = sorted(channels, key=natural_key)
    pos = {c: i for i, c in enumerate(order)}
    n = len(order)

    # 每列一个 m 位综合征掩码；syndrome 为右端观测向量。
    cols = [0] * n
    syndrome = 0
    for j, chk in enumerate(checks):
        bit = 1 << j
        for c in chk["channels"]:
            cols[pos[c]] |= bit
        if chk["parity"] & 1:
            syndrome |= bit

    # 按排序通道折半
    n1 = n // 2
    n2 = n - n1
    left_cols, right_cols = cols[:n1], cols[n1:]
    rev_l = _bit_reverse_table(n1)
    rev_r = _bit_reverse_table(n2)

    # 左半：按综合征建立索引。值打包为整数 (weight << 2n1) | (rev << n1) | mask，
    # 整数越小代表 (重量, 字典序键) 越优；每个综合征只保留最优候选。
    index: dict[int, int] = {}
    for mask, syn in enumerate(_syndromes(left_cols)):
        packed = (mask.bit_count() << (2 * n1)) | (rev_l[mask] << n1) | mask
        old = index.get(syn)
        if old is None or packed < old:
            index[syn] = packed

    # 右半：精确合并。总键 = (总重量 << (n1+n2)) | (左rev << n2) | 右rev，
    # 与全局裁决顺序 (总重量, 升序选择向量字典序) 一致。
    mask_limit_l = (1 << n1) - 1
    best_key = None
    best_masks = (0, 0)
    for mask_r, syn_r in enumerate(_syndromes(right_cols)):
        left = index.get(syndrome ^ syn_r)
        if left is None:
            continue
        total_weight = (left >> (2 * n1)) + mask_r.bit_count()
        key = (
            (total_weight << (n1 + n2))
            | (((left >> n1) & mask_limit_l) << n2)
            | rev_r[mask_r]
        )
        if best_key is None or key < best_key:
            best_key = key
            best_masks = (left & mask_limit_l, mask_r)

    if best_key is None:
        # 两侧无法合并出任何候选：精确判定不可行
        return SolveResult(False, None, None, None)

    mask_l, mask_r = best_masks
    selection = [(mask_l >> i) & 1 for i in range(n1)] + [
        (mask_r >> i) & 1 for i in range(n2)
    ]
    faults = [order[i] for i in range(n) if selection[i]]
    return SolveResult(True, sum(selection), selection, faults)
