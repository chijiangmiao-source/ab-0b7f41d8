"""求解器代码测试：唯一故障、多解裁决、不可行、与朴素全枚举参照对拍。"""
import itertools
import random

from app.solver import natural_key, solve_min_weight


def _brute_force(channels, checks):
    """朴素全枚举参照（仅测试用）：返回 (是否可行, 最优重量, 最优选择向量)。"""
    order = sorted(channels, key=natural_key)
    pos = {c: i for i, c in enumerate(order)}
    best = None
    for bits in itertools.product((0, 1), repeat=len(order)):
        ok = True
        for chk in checks:
            acc = 0
            for c in chk["channels"]:
                acc ^= bits[pos[c]]
            if acc != (chk["parity"] & 1):
                ok = False
                break
        if not ok:
            continue
        key = (sum(bits), bits)
        if best is None or key < best[0]:
            best = (key, bits)
    if best is None:
        return False, None, None
    bits = best[1]
    return True, sum(bits), list(bits)


def test_unique_fault():
    r = solve_min_weight(["1", "2", "3"], [
        {"channels": ["1", "2"], "parity": 1},
        {"channels": ["2", "3"], "parity": 1},
        {"channels": ["1", "3"], "parity": 0},
    ])
    assert r.feasible
    assert r.weight == 1
    assert r.fault_channels == ["2"]
    assert r.selection == [0, 1, 0]


def test_tie_break_lexicographically_smallest_selection_vector():
    r = solve_min_weight(["1", "2", "3", "4"], [
        {"channels": ["1", "2"], "parity": 1},
        {"channels": ["3", "4"], "parity": 1},
    ])
    assert r.feasible
    assert r.weight == 2
    # 同重候选 {1,3} {1,4} {2,3} {2,4}，升序选择向量字典序最小为 (0,1,0,1)
    assert r.selection == [0, 1, 0, 1]
    assert r.fault_channels == ["2", "4"]


def test_infeasible():
    r = solve_min_weight(["1", "2", "3"], [
        {"channels": ["1", "2"], "parity": 0},
        {"channels": ["1", "3"], "parity": 0},
        {"channels": ["2", "3"], "parity": 1},
    ])
    assert not r.feasible
    assert r.weight is None and r.selection is None and r.fault_channels is None


def test_natural_key_order():
    ids = ["CH10", "1", "CH2", "A", "10"]
    assert sorted(ids, key=natural_key) == ["1", "10", "A", "CH2", "CH10"]


def test_matches_brute_force_random():
    rng = random.Random(20260926)
    for _ in range(80):
        n = rng.randint(2, 10)
        channels = [f"CH{i}" for i in range(1, n + 1)]
        m = rng.randint(1, min(6, 2 ** n - 1))
        seen, checks = set(), []
        while len(checks) < m:
            k = rng.randint(1, n)
            subset = tuple(sorted(rng.sample(channels, k), key=natural_key))
            if subset in seen:
                continue
            seen.add(subset)
            checks.append({"channels": list(subset), "parity": rng.randint(0, 1)})
        r = solve_min_weight(channels, checks)
        feas, w, bits = _brute_force(channels, checks)
        assert r.feasible == feas
        if feas:
            assert r.weight == w
            assert r.selection == bits


def test_large_instance_36_channels_28_checks():
    rng = random.Random(7)
    channels = [f"CH{i:02d}" for i in range(36)]
    order = sorted(channels, key=natural_key)
    pos = {c: i for i, c in enumerate(order)}
    true_x = [0] * 36
    for i in rng.sample(range(36), 3):
        true_x[i] = 1
    checks, seen = [], set()
    while len(checks) < 28:
        k = rng.randint(2, 6)
        subset = tuple(sorted(rng.sample(channels, k)))
        if subset in seen:
            continue
        seen.add(subset)
        parity = 0
        for c in subset:
            parity ^= true_x[pos[c]]
        checks.append({"channels": list(subset), "parity": parity})
    r = solve_min_weight(channels, checks)
    assert r.feasible
    assert r.weight <= 3
    # 返回向量必须同时满足全部约束（全板结论，而非局部满足的解释）
    for chk in checks:
        acc = 0
        for c in chk["channels"]:
            acc ^= r.selection[pos[c]]
        assert acc == chk["parity"]
