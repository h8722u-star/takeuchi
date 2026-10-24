import streamlit as st
import os
import random
import copy
import uuid
import json
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

# --- データ構造 ---

_STRIP_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

def normalize_target_str(s: str) -> str:
    if not s: return ""
    s = s.strip()
    for prefix in ("[キャラ] ", "[部位] ", "[部位名のみ] ", "[キャラ全体] ", "[部位名] "):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    return s

@dataclass
class ActiveBuff:
    stat: str
    amount: int
    duration: int
    source_name: str
    source_char_id: str = ""        # 効果を付与したキャラクターのID
    source_char_name: str = ""      # 効果を付与したキャラクター名
    applied_round: int = 0          # 付与されたラウンド
    last_decremented_round: int = 0 # 最後に効果時間が減算されたラウンド

@dataclass
class EffectDef:
    target: str = "攻撃対象"
    stat: str = "命中力"
    amount: int = 0
    duration: int = 3

@dataclass
class ActionDef:
    id: str
    name: str
    action_type: str = "物理攻撃"
    mp_cost: int = 0
    mp_cost_type: str = "1回固定"
    is_minor_action: bool = False

    use_custom_stats: bool = False
    custom_accuracy: int = 0
    custom_damage: int = 0

    cooldown_turns: int = 0
    current_cooldown: int = 0
    start_turn: int = 1

    target_side: str = "敵" # "敵", "味方", "自身"
    range_pref: str = "前衛優先" # "前衛優先", "後衛優先", "すべての敵を攻撃可能"
    target_count: int = 1   # 0=全体
    heal_threshold: int = 100
    exec_cond: str = "常時 (制限なし)"
    specific_target: str = "" # 特定の対象 (キャラ名/部位名)

    effects: List[EffectDef] = field(default_factory=list)
    is_heal_action: bool = field(default=False, init=False)
    is_magic_or_eff: bool = field(default=False, init=False)
    is_mag_or_heal: bool = field(default=False, init=False)
    norm_spec_target: str = field(default="", init=False)
    spec_tc: str = field(default="", init=False)
    spec_tp: str = field(default="", init=False)

    def __post_init__(self):
        atype = self.action_type
        self.is_heal_action = ("回復" in atype)
        self.is_mag_or_heal = ("魔法" in atype or self.is_heal_action)
        self.is_magic_or_eff = (self.is_mag_or_heal or "効果付与" in atype)
        self.norm_spec_target = normalize_target_str(self.specific_target)
        if " - " in self.norm_spec_target:
            tc, tp = self.norm_spec_target.split(" - ", 1)
            self.spec_tc = tc.strip()
            self.spec_tp = tp.strip()

    def get_accuracy(self, part: 'BodyPart', owner_char: 'Character' = None) -> int:
        if self.use_custom_stats:
            base = self.custom_accuracy
            mod = 0
            if part.active_buffs:
                for b in part.active_buffs:
                    if b.stat == "命中力":
                        mod += b.amount
            if owner_char and part.dep_target and part.dep_acc_bonus:
                if not self.is_mag_or_heal:
                    if is_premise_part_alive(owner_char, part.dep_target, part.dep_target_base):
                        mod += part.dep_acc_bonus
            res = base + mod
            return res if res > 0 else 0
        elif self.is_magic_or_eff:
            mod = 0
            if part.active_buffs:
                for b in part.active_buffs:
                    if b.stat == "命中力":
                        mod += b.amount
            res = part.magic_power + mod
            return res if res > 0 else 0
        else:
            return part.get_accuracy(owner_char)

    def get_damage(self, part: 'BodyPart') -> int:
        base = self.custom_damage if self.use_custom_stats else (part.magic_damage if self.is_mag_or_heal else part.damage_fixed)
        mod = 0
        if part.active_buffs:
            for b in part.active_buffs:
                if b.stat == "打撃点":
                    mod += b.amount
        res = base + mod
        return res if res > 0 else 0

@dataclass
class BodyPart:
    part_id: str
    name: str
    is_core: bool = False

    max_hp: int = 10
    current_hp: int = 10
    max_mp: int = 0
    current_mp: int = 0

    accuracy: int = 0
    evasion: int = 0
    evasion_bonus: int = 0
    defense: int = 0
    damage_fixed: int = 0

    fortitude: int = 0
    willpower: int = 0
    magic_power: int = 0
    magic_damage: int = 0

    basic_action: ActionDef = field(default_factory=lambda: ActionDef(id="b_act", name="通常攻撃"))
    special_actions: List[ActionDef] = field(default_factory=list)

    upkeep_hp: int = 0
    upkeep_mp: int = 0

    dep_part_name: str = ""       # 連動する前提部位名
    dep_eva_bonus: int = 0        # 前提部位生存時の回避ボーナス
    dep_acc_bonus: int = 0        # 前提部位生存時の命中ボーナス
    dep_protect: bool = False     # 前提部位生存中は攻撃対象にならない

    active_buffs: List[ActiveBuff] = field(default_factory=list)
    base_name: str = field(default="", init=False)
    dep_target: str = field(default="", init=False)
    dep_target_base: str = field(default="", init=False)
    minor_actions: List[ActionDef] = field(default_factory=list, init=False)
    heal_actions: List[ActionDef] = field(default_factory=list, init=False)
    spec_tgt_actions: List[ActionDef] = field(default_factory=list, init=False)
    regular_sp_actions: List[ActionDef] = field(default_factory=list, init=False)

    def __post_init__(self):
        self.base_name = (self.name or "").rstrip(_STRIP_ALNUM)
        self.dep_target = (self.dep_part_name or "").strip()
        self.dep_target_base = self.dep_target.rstrip(_STRIP_ALNUM)
        self.minor_actions = [sp for sp in self.special_actions if sp.is_minor_action]
        heals = [sp for sp in self.special_actions if not sp.is_minor_action and sp.is_heal_action]
        heals.sort(key=lambda x: x.cooldown_turns, reverse=True)
        self.heal_actions = heals
        specs = [sp for sp in self.special_actions if not sp.is_minor_action and not sp.is_heal_action and sp.norm_spec_target]
        specs.sort(key=lambda x: x.cooldown_turns, reverse=True)
        self.spec_tgt_actions = specs
        regs = [sp for sp in self.special_actions if not sp.is_minor_action and not sp.is_heal_action and not sp.norm_spec_target]
        regs.sort(key=lambda x: x.cooldown_turns, reverse=True)
        self.regular_sp_actions = regs

    @property
    def is_alive(self) -> bool:
        return self.current_hp > 0

    def get_accuracy(self, owner_char: 'Character' = None) -> int:
        bonus = 0
        if owner_char and self.dep_target and self.dep_acc_bonus:
            if is_premise_part_alive(owner_char, self.dep_target, self.dep_target_base):
                bonus += self.dep_acc_bonus
        if self.active_buffs:
            for b in self.active_buffs:
                if b.stat == "命中力":
                    bonus += b.amount
        res = self.accuracy + bonus
        return res if res > 0 else 0

    def get_evasion(self, owner_char: 'Character' = None) -> int:
        bonus = self.evasion_bonus
        if owner_char and self.dep_target and self.dep_eva_bonus:
            if is_premise_part_alive(owner_char, self.dep_target, self.dep_target_base):
                bonus += self.dep_eva_bonus
        if self.active_buffs:
            for b in self.active_buffs:
                if b.stat == "回避力":
                    bonus += b.amount
        res = self.evasion + bonus
        return res if res > 0 else 0

    def get_defense(self) -> int:
        bonus = 0
        if self.active_buffs:
            for b in self.active_buffs:
                if b.stat == "防護点":
                    bonus += b.amount
        res = self.defense + bonus
        return res if res > 0 else 0

    def get_fortitude(self) -> int:
        bonus = 0
        if self.active_buffs:
            for b in self.active_buffs:
                if b.stat == "生命抵抗力":
                    bonus += b.amount
        res = self.fortitude + bonus
        return res if res > 0 else 0

    def get_willpower(self) -> int:
        bonus = 0
        if self.active_buffs:
            for b in self.active_buffs:
                if b.stat == "精神抵抗力":
                    bonus += b.amount
        res = self.willpower + bonus
        return res if res > 0 else 0

MP_EMPTY_BEHAVIOR_OPTS = [
    "前衛に移動して攻撃に参加する",
    "そのまま後衛にとどまる"
]

@dataclass
class Character:
    char_id: str
    name: str
    position: str = "前衛" # "前衛" or "後衛"
    parts: List[BodyPart] = field(default_factory=list)
    is_fixed_roll: bool = False
    target_strategy: str = "ランダム"
    mp_empty_behavior: str = "前衛に移動して攻撃に参加する"
    initial_position: str = field(default="前衛", init=False)
    base_name: str = field(default="", init=False)
    core_parts: List[BodyPart] = field(default_factory=list, init=False)
    check_parts: List[BodyPart] = field(default_factory=list, init=False)

    def __post_init__(self):
        self.initial_position = self.position
        self.base_name = (self.name or "").rstrip(_STRIP_ALNUM)
        self.core_parts = [p for p in self.parts if p.is_core]
        self.check_parts = self.core_parts if self.core_parts else self.parts

    def is_defeated(self) -> bool:
        for p in self.check_parts:
            if p.current_hp > 0:
                return False
        return True

    def reset(self):
        self.position = self.initial_position
        for p in self.parts:
            p.current_hp = p.max_hp
            p.current_mp = p.max_mp
            if p.active_buffs:
                p.active_buffs.clear()
            p.basic_action.current_cooldown = 0
            for sp in p.special_actions:
                sp.current_cooldown = 0

    def get_alive_parts(self) -> List[BodyPart]:
        return [p for p in self.parts if p.current_hp > 0]

@dataclass
class Party:
    side_name: str
    characters: List[Character] = field(default_factory=list)
    current_round: int = 1

    def is_defeated(self) -> bool:
        for c in self.characters:
            for p in c.check_parts:
                if p.current_hp > 0:
                    return False
        return True

    def get_alive_characters(self) -> List[Character]:
        return [c for c in self.characters if not c.is_defeated()]

    def get_all_alive_parts(self) -> List[Tuple[Character, BodyPart]]:
        res = []
        for c in self.characters:
            alive_char = False
            for cp in c.check_parts:
                if cp.current_hp > 0:
                    alive_char = True
                    break
            if alive_char:
                for p in c.parts:
                    if p.current_hp > 0:
                        res.append((c, p))
        return res

    def reset(self):
        self.current_round = 1
        for c in self.characters:
            c.reset()

# --- ロジック ---
def is_premise_part_alive(owner_char: Optional['Character'], dep_part_name: str, target_base: Optional[str] = None) -> bool:
    if not owner_char or not dep_part_name:
        return False
    if target_base is None:
        target = dep_part_name.strip()
        if not target:
            return False
        target_base = target.rstrip(_STRIP_ALNUM)
    else:
        target = dep_part_name
    for p in owner_char.parts:
        if p.current_hp <= 0:
            continue
        p_name = p.name
        p_base = p.base_name
        if p_name == target or p_base == target or (target_base and p_base == target_base):
            return True
    return False

_randint = random.randint
_uniform = random.uniform

def roll_2d6() -> int:
    return _randint(1, 6) + _randint(1, 6)

def check_heal_condition(party: Party, action: ActionDef) -> bool:
    if not action.is_heal_action:
        return True
    th = action.heal_threshold / 100.0
    for c in party.characters:
        if not c.is_defeated():
            for p in c.parts:
                if 0 < p.current_hp < p.max_hp and (p.current_hp / p.max_hp <= th):
                    return True
    return False

def is_part_matching_specific_target(c: Character, p: BodyPart, specific_target: str, tc: str = "", tp: str = "") -> bool:
    if not specific_target:
        return False
    c_name = c.name
    p_name = p.name
    c_base = c.base_name
    p_base = p.base_name

    # 1. 複合表記: "キャラ名 - 部位名"
    if tc or " - " in specific_target:
        if not tc:
            target = normalize_target_str(specific_target)
            if " - " in target:
                s_tc, s_tp = target.split(" - ", 1)
                tc = s_tc.strip()
                tp = s_tp.strip()
            else:
                specific_target = target
        if tc:
            c_matches = (c_name == tc or c_base == tc or tc in c_name or c_name in tc)
            p_matches = (p_name == tp or p_base == tp or (len(tp) >= 2 and tp in p_name))
            return bool(c_matches and p_matches)

    target = specific_target
    if target.startswith("["):
        target = normalize_target_str(target)
        if not target:
            return False

    # 2. 部位名の完全一致・ベース名一致 / 3. キャラ名の完全一致・ベース名一致
    if p_name == target or p_base == target or c_name == target or c_base == target:
        return True

    # 4. キャラ名・部位名への部分一致
    if len(target) >= 2:
        if target in c_name or target in c_base or target in p_name or target in p_base:
            return True

    return False

def has_alive_matching_target(target_party: Party, action: ActionDef) -> bool:
    spec_tgt = action.norm_spec_target
    if not spec_tgt:
        return True
    tc = action.spec_tc
    tp = action.spec_tp
    for c in target_party.characters:
        if not c.is_defeated():
            for p in c.parts:
                if p.current_hp <= 0:
                    continue
                if p.dep_protect and p.dep_target and is_premise_part_alive(c, p.dep_target, p.dep_target_base):
                    continue
                if is_part_matching_specific_target(c, p, spec_tgt, tc, tp):
                    return True
    return False

def can_execute_action_conditions(attacker: Character, atk_party: Party, action: ActionDef, current_round: int = 1, def_party: Optional[Party] = None) -> bool:
    if current_round < action.start_turn:
        return False

    if action.norm_spec_target and def_party is not None:
        target_party = atk_party if action.target_side in ("味方", "自身") else def_party
        if not has_alive_matching_target(target_party, action):
            return False

    cond = action.exec_cond
    if cond == "常時 (制限なし)":
        return True

    if "自陣前衛が全滅時のみ" in cond or "前衛が全滅時のみ" in cond:
        if getattr(attacker, "initial_position", attacker.position) == "後衛" and attacker.position == "前衛":
            pass
        else:
            for c in atk_party.characters:
                if c.position == '前衛' and not c.is_defeated():
                    return False

    elif "自陣前衛が生存中のみ" in cond or "前衛が生存中のみ" in cond:
        has_other_front = False
        for c in atk_party.characters:
            if c != attacker and c.position == '前衛' and not c.is_defeated():
                has_other_front = True
                break
        if not has_other_front:
            return False

    elif "自身HP50%以下時のみ" in cond or "HP50%以下" in cond:
        target_parts = attacker.check_parts
        total_cur = sum(p.current_hp for p in target_parts)
        total_max = sum(p.max_hp for p in target_parts)
        if total_max > 0 and (total_cur / total_max) > 0.5:
            return False

    return True

def get_max_affordable_targets(part: BodyPart, action: ActionDef) -> int:
    if action.mp_cost == 0: return 999
    if action.mp_cost_type == "対象数に比例":
        return part.current_mp // action.mp_cost
    else:
        return 999 if part.current_mp >= action.mp_cost else 0

def sort_targets_by_vulnerability(atk_part: BodyPart, action: ActionDef, pool: List[Tuple[Character, BodyPart]], attacker: Character = None) -> List[Tuple[Character, BodyPart]]:
    if not pool: return []
    scored_parts = []
    act_type = action.action_type
    is_effect_only = "効果付与のみ" in act_type
    is_phys_or_gun = act_type in ("物理攻撃", "ガン(回避/防護点無視)")
    is_auto = "自動成功" in act_type
    is_fort = "生命抵抗" in act_type
    is_half_type = "半減" in act_type
    atk_acc = action.get_accuracy(atk_part, attacker)
    atk_dmg = action.get_damage(atk_part)

    for c, p in pool:
        if is_phys_or_gun:
            hit_diff = atk_acc - p.get_evasion(c)
            def_val = p.get_defense() if act_type == "物理攻撃" else 0
            expected_dmg = max(1.0, float(atk_dmg + 7 - def_val)) if not is_effect_only else 1.0
        elif is_auto:
            hit_diff = 100
            expected_dmg = 1.0
        else:
            res_target = p.get_fortitude() if is_fort else p.get_willpower()
            hit_diff = atk_acc - res_target
            expected_dmg = max(1.0, float(atk_dmg + 7)) if not is_effect_only else 1.0

        hit_rate = max(0.1, min(1.0, 0.58 + hit_diff * 0.1))
        expected_dps = hit_rate * expected_dmg
        if is_half_type:
            expected_dps += (1 - hit_rate) * int(expected_dmg / 2 + 0.5)

        score = expected_dps / p.current_hp + _uniform(0.0001, 0.0009)
        scored_parts.append((score, c, p))

    scored_parts.sort(key=lambda x: x[0], reverse=True)
    return [(c, p) for s, c, p in scored_parts]

def select_targets(attacker: Character, atk_part: BodyPart, atk_party: Party, def_party: Party, action: ActionDef, max_affordable: int = 999) -> List[Tuple[Character, BodyPart]]:
    target_party = atk_party if action.target_side in ("味方", "自身") else def_party
    targets_pool = target_party.get_all_alive_parts()

    if not targets_pool or max_affordable <= 0: return []
    if action.target_side == "自身": return [(attacker, atk_part)]

    # 敵対象時の特殊処理
    if action.target_side == "敵":
        # 1. 前提部位による「保護（攻撃対象不可）」フィルタリング
        unprotected_pool = []
        for c, p in targets_pool:
            if p.dep_protect and p.dep_target:
                if is_premise_part_alive(c, p.dep_target, p.dep_target_base):
                    continue
            unprotected_pool.append((c, p))
        if unprotected_pool:
            targets_pool = unprotected_pool

    # 2. 特定対象のフィルタリング（指定がある場合、通常の射程に優先してその対象を絞り込む）
    spec_tgt = action.norm_spec_target
    if spec_tgt:
        tc = action.spec_tc
        tp = action.spec_tp
        spec_pool = [
            (c, p) for c, p in targets_pool
            if is_part_matching_specific_target(c, p, spec_tgt, tc, tp)
        ]
        if not spec_pool:
            return []
        targets_pool = spec_pool
        is_area_attack = ("全体" in action.range_pref or "全員" in action.range_pref)
    else:
        # 射程・範囲判定 (エリア全体・全域攻撃)
        is_area_attack = False
        r_pref = action.range_pref
        if r_pref == "前衛エリア全体":
            front_pool = [(c, p) for c, p in targets_pool if c.position == '前衛']
            if front_pool:
                targets_pool = front_pool
            is_area_attack = True
        elif r_pref == "後衛エリア全体":
            back_pool = [(c, p) for c, p in targets_pool if c.position == '後衛']
            if back_pool:
                targets_pool = back_pool
            is_area_attack = True
        elif "全エリア全体" in r_pref or "戦場のすべての敵" in r_pref or "味方全員" in r_pref:
            is_area_attack = True
        elif action.target_side == "敵":
            if r_pref == "前衛優先":
                front_pool = [(c, p) for c, p in targets_pool if c.position == '前衛']
                if front_pool:
                    targets_pool = front_pool
            elif r_pref == "後衛優先":
                back_pool = [(c, p) for c, p in targets_pool if c.position == '後衛']
                if back_pool:
                    targets_pool = back_pool

    if not targets_pool:
        return []

    # エリア全体攻撃の場合は、範囲内の対象全員を選択
    if is_area_attack:
        if action.is_heal_action:
            needy = [(c, p) for c, p in targets_pool if p.current_hp < p.max_hp]
            if not needy: return []
            return needy[:max_affordable]
        return targets_pool[:max_affordable]

    count = action.target_count if action.target_count > 0 else len(targets_pool)
    count = min(count, max_affordable)

    if action.is_heal_action:
        needy = [(c, p) for c, p in targets_pool if p.current_hp < p.max_hp]
        if not needy: return []
        needy.sort(key=lambda cp: cp[1].current_hp / cp[1].max_hp)
        return needy[:count]

    strategy = attacker.target_strategy
    if strategy == "コア部位優先":
        core_targets = [(c, p) for c, p in targets_pool if p.is_core]
        non_core_targets = [(c, p) for c, p in targets_pool if not p.is_core]

        sorted_cores = sort_targets_by_vulnerability(atk_part, action, core_targets, attacker)
        sorted_non_cores = sort_targets_by_vulnerability(atk_part, action, non_core_targets, attacker)
        selected = sorted_cores + sorted_non_cores
        return selected[:count]

    elif strategy == "倒しやすい部位優先":
        selected = sort_targets_by_vulnerability(atk_part, action, targets_pool, attacker)
        return selected[:count]

    else:
        selected = targets_pool.copy()
        random.shuffle(selected)
        return selected[:count]

def format_target_name(char: Character, part: BodyPart) -> str:
    pos_tag = f"[{char.position}]" if char.position else ""
    if char.name == part.name: return f"{pos_tag}{part.name}"
    return f"{pos_tag}{char.name}({part.name})"

def execute_action_multi(attacker: Character, atk_part: BodyPart, atk_party: Party, def_party: Party, target_parts: List[Tuple[Character, BodyPart]], action: ActionDef, enable_log: bool = True, is_fallback: bool = False, current_round: int = 1) -> List[str]:
    log = []
    if not target_parts: return log

    if enable_log:
        act_type_tag = "(補助動作)" if action.is_minor_action else "(主動作)"
        act_name_ui = f"【{action.name}】{act_type_tag}"
        atk_name = format_target_name(attacker, atk_part)

    actual_mp_cost = action.mp_cost
    if action.mp_cost_type == "対象数に比例":
        actual_mp_cost = action.mp_cost * len(target_parts)

    if actual_mp_cost > 0:
        if atk_part.current_mp >= actual_mp_cost:
            atk_part.current_mp -= actual_mp_cost
            if enable_log: log.append(f"💧 {atk_party.side_name}の {atk_name} はMPを {actual_mp_cost} 消費した！ (残りMP: {atk_part.current_mp})")
        else:
            if enable_log: log.append(f"⚠️ {atk_party.side_name}の {atk_name} はMPが足りず、{act_name_ui} を使用できない！ (必要MP:{actual_mp_cost})")
            if not is_fallback and action.id != atk_part.basic_action.id and not action.is_minor_action:
                if enable_log: log.append(f"   ➔ 代わりに基本行動【{atk_part.basic_action.name}】を行う！")
                fb_max = get_max_affordable_targets(atk_part, atk_part.basic_action)
                fb_targets = select_targets(attacker, atk_part, atk_party, def_party, atk_part.basic_action, max_affordable=fb_max)
                return log + execute_action_multi(attacker, atk_part, atk_party, def_party, fb_targets, atk_part.basic_action, enable_log, is_fallback=True, current_round=current_round)
            return log

    if action.cooldown_turns > 0:
        action.current_cooldown = action.cooldown_turns

    act_type = action.action_type
    is_effect_only = "効果付与のみ" in act_type
    is_heal = "回復魔法" in act_type
    is_auto_success = "自動成功" in act_type or is_heal
    is_gun = act_type == "ガン(回避/防護点無視)"
    is_physical = act_type == "物理攻撃" or is_gun
    is_magic = not is_physical and not is_effect_only and not is_heal

    atk_base_val = action.get_accuracy(atk_part, attacker)
    if enable_log:
        atk_stat_name = "命中力" if is_physical else "魔力(行使判定)"
        action_icon = "🪄" if (is_magic or is_heal or is_effect_only) else ("🔫" if is_gun else "⚔️")
        target_names_str = "、".join([format_target_name(c, p) for c, p in target_parts])
    atk_val = 0

    if is_auto_success:
        atk_val = float('inf')
        if enable_log: log.append(f"{action_icon} **{atk_party.side_name}の {atk_name} の {act_name_ui}！** [対象:{target_names_str}] (自動成功)")
    else:
        if attacker.is_fixed_roll:
            atk_val = atk_base_val + 7
            if enable_log: log.append(f"{action_icon} **{atk_party.side_name}の {atk_name} の {act_name_ui}！** [対象:{target_names_str}] [固定値] {atk_stat_name}({atk_base_val})+7 = 達成値:{atk_val}")
        else:
            atk_roll = roll_2d6()
            atk_val = atk_base_val + atk_roll
            if atk_roll == 2:
                if enable_log: log.append(f"{action_icon} **{atk_party.side_name}の {atk_name} の {act_name_ui}！** [対象:{target_names_str}] 2d6[{atk_roll}] -> **自動失敗！**")
                return log
            elif atk_roll == 12:
                if enable_log: log.append(f"{action_icon} **{atk_party.side_name}の {atk_name} の {act_name_ui}！** [対象:{target_names_str}] 2d6[{atk_roll}] -> **自動成功！**")
                atk_val = float('inf')
            else:
                if enable_log: log.append(f"{action_icon} **{atk_party.side_name}の {atk_name} の {act_name_ui}！** [対象:{target_names_str}] 2d6[{atk_roll}] + {atk_stat_name}({atk_base_val}) = 達成値:{atk_val}")

    dmg_base = action.get_damage(atk_part)
    damage_roll = roll_2d6() if not attacker.is_fixed_roll else 7
    raw_damage = damage_roll + dmg_base

    for eff in action.effects:
        if eff.target == "自身":
            buff = ActiveBuff(
                stat=eff.stat,
                amount=eff.amount,
                duration=eff.duration,
                source_name=action.name,
                source_char_id=attacker.char_id,
                source_char_name=attacker.name,
                applied_round=current_round,
                last_decremented_round=current_round
            )
            atk_part.active_buffs.append(buff)
            if enable_log:
                icon = "🟢" if buff.amount > 0 else "🔴"
                log.append(f"   {icon} {atk_name} に効果付与：{buff.stat} {buff.amount:+} ({buff.duration}T)")

    is_fort_act = "生命" in act_type
    is_half_act = "半減" in act_type

    for def_char, def_part in target_parts:
        if def_part.current_hp <= 0: continue
        if enable_log:
            def_name = format_target_name(def_char, def_part)

        if is_heal:
            heal_amount = raw_damage
            def_part.current_hp = min(def_part.max_hp, def_part.current_hp + heal_amount)
            if enable_log:
                log_dmg_roll = f"2d6[{damage_roll}]" if not attacker.is_fixed_roll else "[固定値7]"
                log.append(f"➔ [対象:{def_name}] **回復！** {log_dmg_roll} + 回復力({dmg_base}) = {heal_amount}。**HPが {heal_amount} 回復した！** (現在HP: {def_part.current_hp}/{def_part.max_hp})")

            for eff in action.effects:
                if eff.target == "攻撃対象":
                    buff = ActiveBuff(
                        stat=eff.stat,
                        amount=eff.amount,
                        duration=eff.duration,
                        source_name=action.name,
                        source_char_id=attacker.char_id,
                        source_char_name=attacker.name,
                        applied_round=current_round,
                        last_decremented_round=current_round
                    )
                    def_part.active_buffs.append(buff)
                    if enable_log:
                        icon = "🟢" if buff.amount > 0 else "🔴"
                        log.append(f"   {icon} {def_name} に効果付与：{buff.stat} {buff.amount:+} ({buff.duration}T)")
            continue

        if is_physical:
            def_base_val = def_part.get_evasion(def_char)
            if enable_log:
                eva_bonus_info = ""
                if def_part.dep_target and is_premise_part_alive(def_char, def_part.dep_target, def_part.dep_target_base):
                    if def_part.dep_eva_bonus != 0:
                        eva_bonus_info = f"[{def_part.dep_part_name}生存{def_part.dep_eva_bonus:+}]"
                def_stat_name = f"回避力{eva_bonus_info}"
        else:
            def_base_val = def_part.get_fortitude() if is_fort_act else def_part.get_willpower()
            if enable_log:
                def_stat_name = "生命抵抗力" if is_fort_act else "精神抵抗力"

        if def_char.is_fixed_roll:
            def_val = def_base_val + 7
            if enable_log: log.append(f"🛡️ [対象:{def_name}] の防御！ [固定値] {def_stat_name}({def_base_val})+7 = 達成値:{def_val}")
        else:
            def_roll = roll_2d6()
            def_val = def_base_val + def_roll
            if def_roll == 2:
                if enable_log: log.append(f"🛡️ [対象:{def_name}] の防御！ 2d6[{def_roll}] -> **自動失敗！**")
                def_val = -float('inf')
            elif def_roll == 12:
                if enable_log: log.append(f"🛡️ [対象:{def_name}] の防御！ 2d6[{def_roll}] -> **自動成功！**")
                def_val = float('inf')
            else:
                if enable_log: log.append(f"🛡️ [対象:{def_name}] の防御！ 2d6[{def_roll}] + {def_stat_name}({def_base_val}) = 達成値:{def_val}")

        is_hit = False
        is_half = False
        if atk_val > def_val:
            is_hit = True
        elif is_half_act:
            is_half = True

        if not is_effect_only:
            actual_damage = 0
            if is_hit and not is_half:
                if act_type == "物理攻撃":
                    actual_damage = max(0, raw_damage - def_part.get_defense())
                    if enable_log: log.append(f"➔ [対象:{def_name}] **命中！** {raw_damage}点。防護点軽減で **{actual_damage} 点の物理ダメージ！**")
                else:
                    actual_damage = raw_damage
                    if enable_log:
                        kind = "魔法" if is_magic else "ガン(貫通)"
                        log.append(f"➔ [対象:{def_name}] **直撃！** {raw_damage}点。防護点無視で **{actual_damage} 点の{kind}ダメージ！**")
            elif is_half:
                actual_damage = (raw_damage + 1) // 2
                if enable_log: log.append(f"➔ [対象:{def_name}] **抵抗成功！(半減)** {raw_damage}点。半減(切上)で **{actual_damage} 点の魔法ダメージ！**")
            else:
                if enable_log:
                    if is_physical: log.append(f"➔ [対象:{def_name}] **回避成功！** ダメージを与えられない。")
                    else: log.append(f"➔ [対象:{def_name}] **抵抗成功！(消滅)** 完全に防いだ。")

            if actual_damage > 0:
                def_part.current_hp -= actual_damage
                if enable_log:
                    log.append(f"   (残りHP: {def_part.current_hp})")
                    if def_part.current_hp <= 0: log.append(f"💥 **{def_name} は破壊された！**")

        for eff in action.effects:
            if eff.target == "攻撃対象" and is_hit and not is_half:
                buff = ActiveBuff(
                    stat=eff.stat,
                    amount=eff.amount,
                    duration=eff.duration,
                    source_name=action.name,
                    source_char_id=attacker.char_id,
                    source_char_name=attacker.name,
                    applied_round=current_round,
                    last_decremented_round=current_round
                )
                def_part.active_buffs.append(buff)
                if enable_log:
                    icon = "🟢" if buff.amount > 0 else "🔴"
                    log.append(f"   {icon} {def_name} に効果付与：{buff.stat} {buff.amount:+} ({buff.duration}T)")

    return log

def process_buff_expiry_for_source(source_char: Character, parties: List[Party], current_round: int, enable_log: bool) -> List[str]:
    logs = []
    src_id = source_char.char_id
    for party in parties:
        for c in party.characters:
            for p in c.parts:
                if not p.active_buffs:
                    continue
                active_next = []
                for b in p.active_buffs:
                    if b.source_char_id == src_id and b.duration > 0:
                        if b.last_decremented_round < current_round:
                            b.duration -= 1
                            b.last_decremented_round = current_round
                            if b.duration <= 0:
                                if enable_log and p.current_hp > 0:
                                    logs.append(f"⏳ {party.side_name}の {format_target_name(c, p)} の【{b.source_name}】の効果（{b.stat}）が切れた。")
                                continue
                    active_next.append(b)
                p.active_buffs = active_next
    return logs

def _pick_main_action_and_targets(attacker: Character, atk_part: BodyPart, atk_party: Party, def_party: Party, current_round: int) -> Tuple[Optional[ActionDef], List[Tuple[Character, BodyPart]]]:
    b_act = atk_part.basic_action

    # A. 回復主動作候補のチェック (定期行動 & 基本行動)
    if atk_part.heal_actions:
        for sp in atk_part.heal_actions:
            if sp.current_cooldown <= 0:
                if can_execute_action_conditions(attacker, atk_party, sp, current_round, def_party=def_party) and check_heal_condition(atk_party, sp):
                    max_tgt = get_max_affordable_targets(atk_part, sp)
                    if max_tgt > 0:
                        targets = select_targets(attacker, atk_part, atk_party, def_party, sp, max_affordable=max_tgt)
                        if targets:
                            return sp, targets
    if b_act.is_heal_action:
        if can_execute_action_conditions(attacker, atk_party, b_act, current_round, def_party=def_party) and check_heal_condition(atk_party, b_act):
            max_tgt = get_max_affordable_targets(atk_part, b_act)
            if max_tgt > 0:
                targets = select_targets(attacker, atk_part, atk_party, def_party, b_act, max_affordable=max_tgt)
                if targets:
                    return b_act, targets

    # B. 回復に次ぐ第2優先度: 特定部位・特定キャラが指定されている行動 (対象が生存中のみ実行)
    if atk_part.spec_tgt_actions:
        for sp in atk_part.spec_tgt_actions:
            if sp.current_cooldown <= 0:
                if can_execute_action_conditions(attacker, atk_party, sp, current_round, def_party=def_party):
                    max_tgt = get_max_affordable_targets(atk_part, sp)
                    if max_tgt > 0:
                        targets = select_targets(attacker, atk_part, atk_party, def_party, sp, max_affordable=max_tgt)
                        if targets:
                            return sp, targets
    if b_act.norm_spec_target and not b_act.is_heal_action:
        if can_execute_action_conditions(attacker, atk_party, b_act, current_round, def_party=def_party):
            max_tgt = get_max_affordable_targets(atk_part, b_act)
            if max_tgt > 0:
                targets = select_targets(attacker, atk_part, atk_party, def_party, b_act, max_affordable=max_tgt)
                if targets:
                    return b_act, targets

    # C. 通常の定期行動選択 (特定部位指定のない大技、使用間隔優先)
    if atk_part.regular_sp_actions:
        for sp in atk_part.regular_sp_actions:
            if sp.current_cooldown <= 0:
                if can_execute_action_conditions(attacker, atk_party, sp, current_round, def_party=def_party):
                    max_tgt = get_max_affordable_targets(atk_part, sp)
                    if max_tgt > 0:
                        targets = select_targets(attacker, atk_part, atk_party, def_party, sp, max_affordable=max_tgt)
                        if targets:
                            return sp, targets

    # D. 基本行動へのフォールバック (特定部位指定がない通常攻撃)
    if not b_act.norm_spec_target and not b_act.is_heal_action:
        if can_execute_action_conditions(attacker, atk_party, b_act, current_round, def_party=def_party):
            max_tgt = get_max_affordable_targets(atk_part, b_act)
            if max_tgt > 0:
                targets = select_targets(attacker, atk_part, atk_party, def_party, b_act, max_affordable=max_tgt)
                if targets:
                    return b_act, targets

    return None, []

def process_turn_for_part(attacker: Character, atk_part: BodyPart, atk_party: Party, def_party: Party, enable_log: bool, current_round: int = 1) -> List[str]:
    part_logs = []

    # 1. 補助動作 (is_minor_action) の実行
    if atk_part.minor_actions:
        for sp in atk_part.minor_actions:
            if sp.current_cooldown <= 0:
                if not can_execute_action_conditions(attacker, atk_party, sp, current_round, def_party=def_party): continue
                if not check_heal_condition(atk_party, sp): continue
                max_tgt = get_max_affordable_targets(atk_part, sp)
                if max_tgt > 0:
                    targets = select_targets(attacker, atk_part, atk_party, def_party, sp, max_affordable=max_tgt)
                    if targets: part_logs.extend(execute_action_multi(attacker, atk_part, atk_party, def_party, targets, sp, enable_log, current_round=current_round))

    # 2. 主動作の決定 (事前ソート済みリストにより、条件を満たした最初の行動で即時決定)
    main_action, targets = _pick_main_action_and_targets(attacker, atk_part, atk_party, def_party, current_round)

    # 後衛キャラクターがMP切れ等で行動できない場合、設定に応じて前衛へ移動して攻撃に参加
    if main_action is None and attacker.position == "後衛" and "前衛に移動" in getattr(attacker, "mp_empty_behavior", "前衛に移動して攻撃に参加する"):
        mp_main_actions = [
            act for act in ([atk_part.basic_action] + atk_part.special_actions)
            if not act.is_minor_action and act.mp_cost > 0
        ]
        has_mp_shortage = False
        if mp_main_actions:
            if all(get_max_affordable_targets(atk_part, act) <= 0 for act in mp_main_actions):
                has_mp_shortage = True
            else:
                for act in mp_main_actions:
                    if act.current_cooldown <= 0 and can_execute_action_conditions(attacker, atk_party, act, current_round, def_party=def_party) and check_heal_condition(atk_party, act):
                        if get_max_affordable_targets(atk_part, act) <= 0:
                            has_mp_shortage = True
                            break
        elif atk_part.max_mp > 0 and atk_part.current_mp <= 0:
            has_mp_shortage = True

        if has_mp_shortage and len(attacker.parts) > 1:
            for other_p in attacker.parts:
                if other_p is not atk_part and other_p.current_hp > 0:
                    o_act, o_tgts = _pick_main_action_and_targets(attacker, other_p, atk_party, def_party, current_round)
                    if o_act is not None and o_tgts:
                        has_mp_shortage = False
                        break

        if has_mp_shortage:
            prev_name = format_target_name(attacker, atk_part) if enable_log else ""
            mp_reason = "MPが尽きた" if atk_part.current_mp <= 0 else f"MPが不足した(残りMP:{atk_part.current_mp})"
            attacker.position = "前衛"
            main_action, targets = _pick_main_action_and_targets(attacker, atk_part, atk_party, def_party, current_round)
            if main_action is not None and targets:
                if enable_log:
                    part_logs.append(f"🏃 **{atk_party.side_name}の {prev_name} は{mp_reason}ため、【前衛】へ移動して攻撃に参加した！**")
            else:
                attacker.position = "後衛"

    if main_action is not None and targets:
        part_logs.extend(execute_action_multi(attacker, atk_part, atk_party, def_party, targets, main_action, enable_log, current_round=current_round))

    return part_logs

def run_single_turn(pc_party: Party, enemy_party: Party, enable_log: bool = True, turn_num: Optional[int] = None) -> Tuple[List[str], bool]:
    if turn_num is not None:
        current_round = turn_num
        pc_party.current_round = turn_num
        enemy_party.current_round = turn_num
    else:
        current_round = pc_party.current_round

    logs = []
    both_parties = (pc_party, enemy_party)
    for party in both_parties:
        for c, p in party.get_all_alive_parts():
            # 付与元キャラのない孤立バフのフォールバック減算 ＆ 毒・MP増減の集計
            if p.active_buffs:
                active_next = []
                mod_uphp = 0
                mod_upmp = 0
                for b in p.active_buffs:
                    if not b.source_char_id and b.duration > 0:
                        b.duration -= 1
                        if b.duration <= 0:
                            if enable_log: logs.append(f"⏳ {party.side_name}の {format_target_name(c, p)} の【{b.source_name}】の効果（{b.stat}）が切れた。")
                            continue
                    active_next.append(b)
                    if b.stat == "毎ターンHP減少(毒)":
                        mod_uphp += b.amount
                    elif b.stat == "毎ターンMP減少":
                        mod_upmp += b.amount
                p.active_buffs = active_next
            else:
                mod_uphp = 0
                mod_upmp = 0

            total_uphp = p.upkeep_hp + mod_uphp
            total_upmp = p.upkeep_mp + mod_upmp

            if total_uphp > 0:
                p.current_hp -= total_uphp
                if enable_log:
                    t_name = format_target_name(c, p)
                    logs.append(f"💧 {party.side_name}の {t_name} は毒などでHPを {total_uphp} 失った。(残りHP: {p.current_hp})")
                    if p.current_hp <= 0: logs.append(f"💥 **{t_name} は破壊された！**")
            elif total_uphp < 0:
                heal = -total_uphp
                p.current_hp = min(p.max_hp, p.current_hp + heal)
                if enable_log: logs.append(f"✨ {party.side_name}の {format_target_name(c,p)} はリジェネ等でHPを {heal} 回復した。(残りHP: {p.current_hp})")

            if total_upmp > 0: p.current_mp = max(0, p.current_mp - total_upmp)
            elif total_upmp < 0: p.current_mp = min(p.max_mp, p.current_mp - total_upmp)

            if p.basic_action.current_cooldown > 0: p.basic_action.current_cooldown -= 1
            for sp in p.special_actions:
                if sp.current_cooldown > 0: sp.current_cooldown -= 1

    if pc_party.is_defeated() or enemy_party.is_defeated():
        pc_party.current_round += 1
        enemy_party.current_round += 1
        return logs, True

    for party, opp_party in ((pc_party, enemy_party), (enemy_party, pc_party)):
        for char in party.characters:
            if pc_party.is_defeated() or enemy_party.is_defeated(): break

            # 【重要】そのキャラクターが付与したバフ/デバフの持続時間を、次のターンで行動する直前に1ターン経過
            expiry_logs = process_buff_expiry_for_source(char, both_parties, current_round, enable_log)
            if enable_log and expiry_logs:
                logs.extend(expiry_logs)

            if char.is_defeated():
                continue

            for part in char.parts:
                if part.current_hp <= 0:
                    continue
                if pc_party.is_defeated() or enemy_party.is_defeated(): break
                part_logs = process_turn_for_part(char, part, party, opp_party, enable_log, current_round=current_round)
                if enable_log and part_logs:
                    logs.extend(part_logs)

    is_finished = pc_party.is_defeated() or enemy_party.is_defeated()
    pc_party.current_round += 1
    enemy_party.current_round += 1
    return logs, is_finished

def run_simulation(pc_party_base: Party, enemy_party_base: Party, trials: int):
    pc_wins = 0
    enemy_wins = 0
    total_turns = 0

    pc_party = copy.deepcopy(pc_party_base)
    enemy_party = copy.deepcopy(enemy_party_base)

    for _ in range(trials):
        pc_party.reset()
        enemy_party.reset()

        turns = 0
        while not (pc_party.is_defeated() or enemy_party.is_defeated()):
            turns += 1
            run_single_turn(pc_party, enemy_party, enable_log=False, turn_num=turns)
            if turns >= 1000: break

        total_turns += turns
        if not enemy_party.is_defeated() and pc_party.is_defeated(): enemy_wins += 1
        elif enemy_party.is_defeated() and not pc_party.is_defeated(): pc_wins += 1

    return {
        "trials": trials,
        "pc_wins": pc_wins,
        "enemy_wins": enemy_wins,
        "draws": trials - pc_wins - enemy_wins,
        "win_rate_pc": pc_wins / trials * 100,
        "win_rate_enemy": enemy_wins / trials * 100,
        "avg_turns": total_turns / trials
    }

# --- セーブ・ロード ---
def cleanup_char_keys(char_dict: dict):
    if not isinstance(char_dict, dict): return
    ids_to_remove = set()
    cid = char_dict.get("char_id")
    if cid: ids_to_remove.add(str(cid))
    for p in char_dict.get("parts", []):
        pid = p.get("id")
        if pid: ids_to_remove.add(str(pid))

    for k in list(st.session_state.keys()):
        for target_id in ids_to_remove:
            if target_id in str(k):
                try:
                    del st.session_state[k]
                except KeyError:
                    pass
                break

def cleanup_roster_keys(roster: List[dict]):
    if not roster: return
    for c in roster:
        cleanup_char_keys(c)

def create_default_char(name="新規キャラ") -> dict:
    return {
        "char_id": str(uuid.uuid4()),
        "char_name": name,
        "position": "前衛",
        "strategy": "倒しやすい部位優先",
        "fixed": False,
        "mp_empty_behavior": "前衛に移動して攻撃に参加する",
        "parts": [
            {"id": str(uuid.uuid4()), "name": name, "is_core": True,
             "hp": 20, "mp": 10, "acc": 5, "eva": 5, "dmg": 5, "dfn": 2,
             "fort": 5, "will": 5, "mag": 0, "mag_dmg": 0, "upkeep_hp": 0, "upkeep_mp": 0,
             "b_act": {"name": "攻撃", "type": "物理攻撃", "mp": 0, "mp_type": "1回固定", "use_custom": False, "acc": 0, "dmg": 0, "effects": [], "tgt_side": "敵", "range_pref": "前衛優先", "tgt_count": 1, "heal_th": 100, "exec_cond": "常時 (制限なし)"},
             "sp_acts": []}
        ]
    }

_DEFAULT_PC_JSON = '[{"char_id":"ba5754de-162a-4295-a6cf-2bf09d99dcd1","char_name":"腕利きの傭兵","position":"前衛","strategy":"倒しやすい部位優先","fixed":false,"mp_empty_behavior":"前衛に移動して攻撃に参加する","parts":[{"id":"7d3ae837-59bc-4016-8edd-5c745375f65b","name":"剣","is_core":true,"hp":60,"mp":12,"acc":11,"eva":10,"dmg":14,"dfn":7,"fort":10,"will":9,"mag":0,"mag_dmg":0,"dep_name":"","dep_acc":0,"dep_eva":0,"dep_protect":false,"upkeep_hp":0,"upkeep_mp":0,"b_act":{"name":"剣","type":"物理攻撃","mp":0,"mp_type":"1回固定","is_minor":false,"use_custom":false,"acc":0,"dmg":0,"tgt_side":"敵","range_pref":"前衛優先","tgt_count":1,"heal_th":100,"exec_cond":"常時 (制限なし)","effects":[],"specific_target":""},"sp_acts":[{"name":"全力攻撃I","type":"物理攻撃","mp":0,"mp_type":"1回固定","cooldown":0,"is_minor":false,"use_custom":true,"acc":11,"dmg":18,"tgt_side":"敵","range_pref":"前衛優先","tgt_count":1,"heal_th":100,"exec_cond":"常時 (制限なし)","effects":[{"target":"自身","stat":"回避力","amount":-2,"dur":1}],"start_turn":1,"specific_target":""}]}]},{"char_id":"2cfcd162-397b-49dc-ac83-cd6e931d73f3","char_name":"人族の神官","position":"後衛","strategy":"倒しやすい部位優先","fixed":false,"mp_empty_behavior":"前衛に移動して攻撃に参加する","parts":[{"id":"73b1c0ec-cc0d-4aeb-b77a-8b426a3e0506","name":"武器","is_core":true,"hp":53,"mp":44,"acc":8,"eva":8,"dmg":6,"dfn":5,"fort":9,"will":10,"mag":10,"mag_dmg":10,"dep_name":"","dep_acc":0,"dep_eva":0,"dep_protect":false,"upkeep_hp":0,"upkeep_mp":0,"b_act":{"name":"武器攻撃","type":"物理攻撃","mp":0,"mp_type":"1回固定","is_minor":false,"use_custom":false,"acc":0,"dmg":0,"tgt_side":"敵","range_pref":"前衛優先","tgt_count":1,"heal_th":100,"exec_cond":"自陣前衛が全滅時のみ (後衛の近接など)","effects":[],"specific_target":""},"sp_acts":[{"name":"フォース","type":"魔法(精神抵抗/半減)","mp":4,"mp_type":"対象数に比例","cooldown":0,"is_minor":false,"use_custom":true,"acc":10,"dmg":6,"tgt_side":"敵","range_pref":"前衛優先","tgt_count":1,"heal_th":100,"exec_cond":"常時 (制限なし)","effects":[],"start_turn":1,"specific_target":""},{"name":"キュア・ハート","type":"回復魔法(自身/味方)","mp":5,"mp_type":"対象数に比例","cooldown":0,"is_minor":false,"use_custom":true,"acc":10,"dmg":8,"tgt_side":"味方","tgt_count":10,"heal_th":50,"range_pref":"対象自由(味方)","exec_cond":"常時 (制限なし)","effects":[],"start_turn":2,"specific_target":""},{"name":"セイクリッド・ウェポン","type":"効果付与のみ(自動成功)","mp":3,"mp_type":"対象数に比例","cooldown":18,"start_turn":1,"is_minor":false,"use_custom":false,"acc":10,"dmg":10,"effects":[{"target":"攻撃対象","stat":"命中力","amount":1,"dur":18},{"target":"攻撃対象","stat":"打撃点","amount":2,"dur":18}],"tgt_side":"味方","tgt_count":1,"heal_th":100,"exec_cond":"常時 (制限なし)","range_pref":"前衛優先","specific_target":"腕利きの傭兵 - 剣"},{"name":"セイクリッド・シールド","type":"効果付与のみ(自動成功)","mp":4,"mp_type":"対象数に比例","cooldown":18,"start_turn":2,"is_minor":false,"use_custom":false,"acc":10,"dmg":10,"effects":[{"target":"攻撃対象","stat":"防護点","amount":3,"dur":18}],"tgt_side":"味方","tgt_count":1,"heal_th":100,"exec_cond":"常時 (制限なし)","range_pref":"前衛優先","specific_target":"腕利きの傭兵 - 剣"}]}]}]'
_DEFAULT_EN_JSON = '[{"char_name":"ドレイク(竜形態)","position":"前衛","strategy":"倒しやすい部位優先","fixed":false,"mp_empty_behavior":"前衛に移動して攻撃に参加する","parts":[{"name":"牙(胴体)","is_core":true,"hp":62,"mp":46,"acc":9,"eva":9,"dmg":12,"dfn":7,"fort":10,"will":11,"mag":9,"mag_dmg":9,"dep_name":"翼","dep_acc":1,"dep_eva":1,"dep_protect":false,"upkeep_hp":0,"upkeep_mp":0,"b_act":{"name":"牙","type":"物理攻撃","mp":0,"mp_type":"1回固定","is_minor":false,"use_custom":false,"acc":0,"dmg":0,"tgt_side":"敵","range_pref":"前衛優先","tgt_count":1,"heal_th":100,"effects":[],"exec_cond":"常時 (制限なし)","specific_target":""},"sp_acts":[{"name":"光のブレス","type":"魔法(生命抵抗/半減)","mp":0,"mp_type":"1回固定","cooldown":2,"is_minor":false,"use_custom":true,"acc":8,"dmg":10,"tgt_side":"敵","range_pref":"すべての敵を攻撃可能","tgt_count":1,"heal_th":100,"effects":[],"exec_cond":"常時 (制限なし)","start_turn":1,"specific_target":""},{"name":"スパーク","type":"魔法(精神抵抗/半減)","mp":6,"mp_type":"1回固定","cooldown":4,"is_minor":false,"use_custom":true,"acc":9,"dmg":4,"tgt_side":"敵","range_pref":"前衛エリア全体","tgt_count":0,"heal_th":100,"effects":[],"exec_cond":"常時 (制限なし)","start_turn":1,"specific_target":""}],"id":"6537ca68-3ada-420c-bd94-25aaa69ced99"},{"name":"翼","is_core":false,"hp":38,"mp":16,"acc":11,"eva":7,"dmg":9,"dfn":4,"fort":10,"will":11,"mag":0,"mag_dmg":0,"dep_name":"翼","dep_acc":1,"dep_eva":1,"dep_protect":false,"upkeep_hp":0,"upkeep_mp":0,"b_act":{"name":"翼","type":"物理攻撃","mp":0,"mp_type":"1回固定","is_minor":false,"use_custom":false,"acc":0,"dmg":0,"tgt_side":"敵","range_pref":"前衛優先","tgt_count":1,"heal_th":100,"effects":[],"exec_cond":"常時 (制限なし)","specific_target":""},"sp_acts":[],"id":"89adbed7-6db2-4597-a51e-5e57ac8287ed"},{"name":"翼","is_core":false,"hp":38,"mp":16,"acc":11,"eva":7,"dmg":9,"dfn":4,"fort":10,"will":11,"mag":0,"mag_dmg":0,"dep_name":"翼","dep_acc":1,"dep_eva":1,"dep_protect":false,"upkeep_hp":0,"upkeep_mp":0,"b_act":{"name":"翼","type":"物理攻撃","mp":0,"mp_type":"1回固定","is_minor":false,"use_custom":false,"acc":0,"dmg":0,"tgt_side":"敵","range_pref":"前衛優先","tgt_count":1,"heal_th":100,"effects":[],"exec_cond":"常時 (制限なし)","specific_target":""},"sp_acts":[],"id":"7cb6aa6e-b38d-44d5-8006-3839b1c9d2fd"}],"char_id":"474df075-fc71-43bf-84c4-89cf2c05598b"},{"char_id":"29baaa6d-aaad-46c6-9d48-f647abb1b913","char_name":"トロール","position":"後衛","strategy":"倒しやすい部位優先","fixed":false,"mp_empty_behavior":"前衛に移動して攻撃に参加する","parts":[{"id":"8e21eee4-9d05-46cf-bb03-798aa3f816e5","name":"武器","is_core":true,"hp":50,"mp":26,"acc":9,"eva":7,"dmg":10,"dfn":5,"fort":9,"will":8,"mag":7,"mag_dmg":7,"dep_name":"","dep_acc":0,"dep_eva":0,"dep_protect":false,"upkeep_hp":0,"upkeep_mp":0,"b_act":{"name":"武器攻撃","type":"物理攻撃","mp":0,"mp_type":"1回固定","is_minor":false,"use_custom":false,"acc":0,"dmg":0,"tgt_side":"敵","range_pref":"前衛優先","tgt_count":1,"heal_th":100,"exec_cond":"自陣前衛が全滅時のみ (後衛の近接など)","effects":[],"specific_target":""},"sp_acts":[{"name":"全力攻撃I","type":"物理攻撃","mp":0,"mp_type":"1回固定","cooldown":0,"is_minor":false,"use_custom":true,"acc":9,"dmg":14,"tgt_side":"敵","range_pref":"前衛優先","tgt_count":1,"heal_th":100,"exec_cond":"自陣前衛が全滅時のみ (後衛の近接など)","effects":[{"target":"自身","stat":"回避力","amount":-2,"dur":1}],"start_turn":1,"specific_target":""},{"name":"フォース","type":"魔法(精神抵抗/半減)","mp":4,"mp_type":"対象数に比例","cooldown":0,"is_minor":false,"use_custom":true,"acc":7,"dmg":3,"tgt_side":"敵","range_pref":"前衛優先","tgt_count":1,"heal_th":100,"exec_cond":"常時 (制限なし)","effects":[],"start_turn":1,"specific_target":""},{"name":"キュア・ハート","type":"回復魔法(自身/味方)","mp":5,"mp_type":"対象数に比例","cooldown":0,"is_minor":false,"use_custom":true,"acc":7,"dmg":5,"tgt_side":"味方","tgt_count":1,"heal_th":50,"range_pref":"対象自由(味方)","exec_cond":"常時 (制限なし)","effects":[],"start_turn":2,"specific_target":""},{"name":"ヴァイス・ウェポン","type":"効果付与のみ(自動成功)","mp":3,"mp_type":"対象数に比例","cooldown":18,"start_turn":1,"is_minor":false,"use_custom":false,"acc":10,"dmg":10,"effects":[{"target":"攻撃対象","stat":"命中力","amount":1,"dur":18},{"target":"攻撃対象","stat":"打撃点","amount":3,"dur":18}],"tgt_side":"味方","tgt_count":1,"heal_th":100,"exec_cond":"常時 (制限なし)","range_pref":"前衛優先","specific_target":"ドレイク(竜形態) - 牙(胴体)"},{"name":"ヴァイス・シールド","type":"効果付与のみ(自動成功)","mp":4,"mp_type":"対象数に比例","cooldown":18,"start_turn":2,"is_minor":false,"use_custom":false,"acc":10,"dmg":10,"effects":[{"target":"攻撃対象","stat":"防護点","amount":3,"dur":18}],"tgt_side":"味方","tgt_count":1,"heal_th":100,"exec_cond":"常時 (制限なし)","range_pref":"前衛優先","specific_target":"ドレイク(竜形態) - 牙(胴体)"}]}]}]'

def _load_default_roster(filenames: List[str], fallback_json: str) -> List[dict]:
    data = None
    for p in filenames:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, list) and len(loaded) > 0 and "parts" in loaded[0]:
                    data = copy.deepcopy(loaded)
                    break
            except Exception:
                pass
    if data is None:
        data = json.loads(fallback_json)
    for c in data:
        c["char_id"] = str(uuid.uuid4())
        for part in c.get("parts", []):
            part["id"] = str(uuid.uuid4())
    return data

def get_default_pc_roster() -> List[dict]:
    return _load_default_roster([
        os.path.join(os.path.dirname(__file__), "pc_party.json"),
        r"c:\AI_Workspace\pc_party.json",
        r"c:\AI_Workspace\pc_party (1).json"
    ], _DEFAULT_PC_JSON)

def get_default_enemy_roster() -> List[dict]:
    return _load_default_roster([
        os.path.join(os.path.dirname(__file__), "enemy_party.json"),
        r"c:\AI_Workspace\enemy_party.json",
        r"c:\AI_Workspace\enemy_party (1).json"
    ], _DEFAULT_EN_JSON)

def load_roster_data(uploaded_file, roster: List[dict]):
    try:
        data = json.loads(uploaded_file.getvalue().decode('utf-8'))
        cleanup_roster_keys(roster)
        if isinstance(data, list):
            # パーティ(複数キャラ) または 部位リスト の互換性チェック
            if len(data) > 0 and "parts" in data[0]:
                # すでに roster 形式
                roster.clear()
                for c in data:
                    c_copy = copy.deepcopy(c)
                    c_copy["char_id"] = str(uuid.uuid4())
                    for p in c_copy.get("parts", []): p["id"] = str(uuid.uuid4())
                    roster.append(c_copy)
            else:
                # 昔の parts リスト形式
                roster.clear()
                c_data = copy.deepcopy(data)
                for p in c_data: p["id"] = str(uuid.uuid4())
                roster.append({
                    "char_id": str(uuid.uuid4()),
                    "char_name": c_data[0].get("name", "インポートキャラ") if c_data else "キャラ",
                    "position": "前衛",
                    "strategy": "倒しやすい部位優先",
                    "fixed": False,
                    "parts": c_data
                })
        else:
            # 昔の単一キャラ辞書形式
            roster.clear()
            c_data = copy.deepcopy(data)
            c_data["char_id"] = str(uuid.uuid4())
            for p in c_data.get("parts", []): p["id"] = str(uuid.uuid4())
            roster.append({
                "char_id": c_data["char_id"],
                "char_name": c_data.get("char_name") or c_data.get("name", "名称未設定"),
                "position": c_data.get("position", "前衛"),
                "strategy": c_data.get("strategy", "ランダム"),
                "fixed": c_data.get("fixed", False),
                "parts": c_data.get("parts", [])
            })

        st.success("データの読み込みに成功しました！一番下の「設定を反映してリセット」ボタンを押してください。")
        return True
    except json.JSONDecodeError as e:
        st.error(f"エラー: 正しいJSONフォーマットではありません。({e})")
        return False
    except Exception as e:
        st.error(f"予期せぬエラーが発生しました: {e}")
        return False

def load_single_char_data(uploaded_file, target_char: dict) -> bool:
    try:
        data = json.loads(uploaded_file.getvalue().decode('utf-8'))
        cleanup_char_keys(target_char)
        if isinstance(data, dict):
            if "parts" in data:
                target_char.clear()
                target_char.update(copy.deepcopy(data))
                target_char["char_id"] = str(uuid.uuid4())
                for p in target_char.get("parts", []): p["id"] = str(uuid.uuid4())
                st.success("キャラクターデータを読み込みました！")
                return True
            elif "name" in data and "hp" in data:
                target_char["parts"] = [copy.deepcopy(data)]
                target_char["char_name"] = data.get("name", "読込キャラ")
                target_char["char_id"] = str(uuid.uuid4())
                for p in target_char["parts"]: p["id"] = str(uuid.uuid4())
                st.success("キャラクターデータを読み込みました！")
                return True
        elif isinstance(data, list):
            if len(data) > 0 and "parts" in data[0]:
                target_char.clear()
                target_char.update(copy.deepcopy(data[0]))
                target_char["char_id"] = str(uuid.uuid4())
                for p in target_char.get("parts", []): p["id"] = str(uuid.uuid4())
                st.success("先頭キャラクターデータを読み込みました！")
                return True
            else:
                target_char["parts"] = copy.deepcopy(data)
                target_char["char_id"] = str(uuid.uuid4())
                for p in target_char["parts"]: p["id"] = str(uuid.uuid4())
                st.success("部位データを反映しました！")
                return True
        st.error("対応していないデータ形式です。")
        return False
    except Exception as e:
        st.error(f"読込エラー: {e}")
        return False

def import_roster_from_json(json_str: str, roster: List[dict], replace_all: bool = False) -> bool:
    if not json_str or not json_str.strip():
        st.warning("JSONテキストが入力されていません。")
        return False
    try:
        data = json.loads(json_str)
        if replace_all:
            cleanup_roster_keys(roster)
            roster.clear()

        if isinstance(data, dict):
            # 単一キャラ形式 { "char_name": ..., "parts": [...] }
            c = copy.deepcopy(data)
            c["char_id"] = str(uuid.uuid4())
            if "parts" in c:
                for p in c["parts"]: p["id"] = str(uuid.uuid4())
            else:
                p_data = copy.deepcopy(data)
                p_data["id"] = str(uuid.uuid4())
                c = {
                    "char_id": str(uuid.uuid4()),
                    "char_name": data.get("char_name") or data.get("name", "AIインポートキャラ"),
                    "position": data.get("position", "前衛"),
                    "strategy": data.get("strategy", "倒しやすい部位優先"),
                    "fixed": data.get("fixed", False),
                    "parts": [p_data]
                }
            roster.append(c)
            st.success(f"キャラクター『{c.get('char_name')}』を取り込みました！")
            return True

        elif isinstance(data, list):
            if not data:
                st.warning("データが空です。")
                return False
            if "parts" in data[0]:
                # 複数キャラのリスト [ { "char_name": ..., "parts": [...] }, ... ]
                for c in data:
                    c_copy = copy.deepcopy(c)
                    c_copy["char_id"] = str(uuid.uuid4())
                    for p in c_copy.get("parts", []): p["id"] = str(uuid.uuid4())
                    roster.append(c_copy)
                st.success(f"{len(data)}体のキャラクターを取り込みました！")
                return True
            else:
                # 部位のリスト [ { "name": ..., "hp": ... }, ... ]
                parts_copy = copy.deepcopy(data)
                for p in parts_copy: p['id'] = str(uuid.uuid4())
                c_name = parts_copy[0].get("char_name") or parts_copy[0].get("name", "AIインポートキャラ")
                roster.append({
                    "char_id": str(uuid.uuid4()),
                    "char_name": c_name,
                    "position": "前衛",
                    "strategy": "倒しやすい部位優先",
                    "fixed": False,
                    "parts": parts_copy
                })
                st.success(f"部位データからキャラクター『{c_name}』を作成しました！")
                return True
        st.error("エラー: JSONの形式が正しくありません（オブジェクトまたは配列を指定してください）。")
        return False
    except json.JSONDecodeError as e:
        st.error(f"エラー: 正しいJSONフォーマットではありません。({e})")
        return False
    except Exception as e:
        st.error(f"予期せぬエラーが発生しました: {e}")
        return False

def get_numbered_part_names(parts: List[dict]) -> List[str]:
    counts = {}
    for p in parts:
        name = p.get('name', '部位') or '部位'
        counts[name] = counts.get(name, 0) + 1
    trackers = {name: 0 for name, cnt in counts.items() if cnt > 1}
    result = []
    for p in parts:
        name = p.get('name', '部位') or '部位'
        if name in trackers:
            suffix = chr(ord('A') + trackers[name])
            if suffix > 'Z': suffix = str(trackers[name] - 25)
            result.append(f"{name}{suffix}")
            trackers[name] += 1
        else:
            result.append(name)
    return result

def convert_roster_to_party(roster: List[dict], side_name: str) -> Party:
    # 同名キャラクターの重複カウントと連番付与 (A, B, C...)
    char_counts = {}
    for c_data in roster:
        c_name = c_data.get("char_name", "名称未設定") or "名称未設定"
        char_counts[c_name] = char_counts.get(c_name, 0) + 1
    char_trackers = {name: 0 for name, count in char_counts.items() if count > 1}

    chars = []
    for c_data in roster:
        orig_name = c_data.get("char_name", "名称未設定") or "名称未設定"
        if orig_name in char_trackers:
            suffix = chr(ord('A') + char_trackers[orig_name])
            if suffix > 'Z': suffix = str(char_trackers[orig_name] - 25)
            final_char_name = f"{orig_name}{suffix}"
            char_trackers[orig_name] += 1
        else:
            final_char_name = orig_name

        numbered_parts = get_numbered_part_names(c_data.get("parts", []))

        body_parts = []
        for i, (p, final_name) in enumerate(zip(c_data.get("parts", []), numbered_parts)):

            b_act = p.get('b_act', {})
            b_act_def = ActionDef(
                id=f"b_{i}", name=b_act.get('name', '通常攻撃'), action_type=b_act.get('type', '物理攻撃'),
                mp_cost=b_act.get('mp', 0), mp_cost_type=b_act.get('mp_type', '1回固定'),
                is_minor_action=b_act.get('is_minor', False),
                use_custom_stats=b_act.get('use_custom', False), custom_accuracy=b_act.get('acc', 0), custom_damage=b_act.get('dmg', 0),
                cooldown_turns=0,
                start_turn=b_act.get('start_turn', 1),
                target_side=b_act.get('tgt_side', '敵'), range_pref=b_act.get('range_pref', '前衛優先'), target_count=b_act.get('tgt_count', 1), heal_threshold=b_act.get('heal_th', 100),
                exec_cond=b_act.get('exec_cond', '常時 (制限なし)'),
                specific_target=b_act.get('specific_target', ''),
                effects=[EffectDef(target=e.get('target', '攻撃対象'), stat=e.get('stat', '命中力'), amount=e.get('amount', 0), duration=e.get('dur', 3)) for e in b_act.get('effects', [])]
            )

            sp_acts = []
            for j, sp in enumerate(p.get('sp_acts', [])):
                sp_acts.append(ActionDef(
                    id=f"sp_{i}_{j}", name=sp.get('name', '特殊行動'), action_type=sp.get('type', '魔法(生命抵抗/半減)'),
                    mp_cost=sp.get('mp', 0), mp_cost_type=sp.get('mp_type', '1回固定'),
                    is_minor_action=sp.get('is_minor', False),
                    use_custom_stats=sp.get('use_custom', False), custom_accuracy=sp.get('acc', 0), custom_damage=sp.get('dmg', 0),
                    cooldown_turns=sp.get('cooldown', 0),
                    start_turn=sp.get('start_turn', 1),
                    target_side=sp.get('tgt_side', '敵'), range_pref=sp.get('range_pref', '前衛優先'), target_count=sp.get('tgt_count', 1), heal_threshold=sp.get('heal_th', 100),
                    exec_cond=sp.get('exec_cond', '常時 (制限なし)'),
                    specific_target=sp.get('specific_target', ''),
                    effects=[EffectDef(target=e.get('target', '攻撃対象'), stat=e.get('stat', '命中力'), amount=e.get('amount', 0), duration=e.get('dur', 3)) for e in sp.get('effects', [])]
                ))

            body_parts.append(BodyPart(
                part_id=f"part_{i}", name=final_name, is_core=p.get('is_core', False),
                max_hp=p.get('hp', 10), current_hp=p.get('hp', 10), max_mp=p.get('mp', 0), current_mp=p.get('mp', 0),
                accuracy=p.get('acc', 0), evasion=p.get('eva', 0), damage_fixed=p.get('dmg', 0), defense=p.get('dfn', 0),
                fortitude=p.get('fort', 5), willpower=p.get('will', 4),
                magic_power=p.get('mag', 0), magic_damage=p.get('mag_dmg', 0),
                basic_action=b_act_def, special_actions=sp_acts,
                upkeep_hp=p.get('upkeep_hp', 0), upkeep_mp=p.get('upkeep_mp', 0),
                dep_part_name=p.get('dep_name', ''), dep_eva_bonus=p.get('dep_eva', 0), dep_acc_bonus=p.get('dep_acc', 0), dep_protect=p.get('dep_protect', False)
            ))

        chars.append(Character(
            char_id=c_data["char_id"], name=final_char_name, position=c_data.get("position", "前衛"), parts=body_parts,
            is_fixed_roll=c_data["fixed"], target_strategy=c_data["strategy"],
            mp_empty_behavior=c_data.get("mp_empty_behavior", "前衛に移動して攻撃に参加する")
        ))
    return Party(side_name=side_name, characters=chars)

# --- Streamlit UI ---
act_options = [
    "物理攻撃", "ガン(回避/防護点無視)", "魔法(生命抵抗/半減)", "魔法(精神抵抗/半減)", "魔法(生命抵抗/消滅)", "魔法(精神抵抗/消滅)",
    "回復魔法(自身/味方)", "効果付与のみ(自動成功)", "効果付与のみ(生命抵抗/消滅)", "効果付与のみ(精神抵抗/消滅)"
]

AI_PROMPT_TEMPLATE = """あなたはTRPG『ソード・ワールド2.5（SW2.5）』のルールに精通した熟練のゲームマスター兼データ解析AIです。
添付された魔物（またはキャラクター）のデータ画像を正確に読み取り、以下の【出力仕様】に準拠したJSONデータを生成してください。

---

### 【SW2.5の特殊能力表記ルールの解釈ガイド】

1. **分類マークの解釈**:
   - ◯ (常時型): 常に発揮される能力。パッシブ効果や部位連動（飛翔など）として反映します。
   - ▶ (主動作型): 主動作として実行する強力な攻撃・ブレス・大技など。is_minor: false の行動として登録します。
   - ▶▶ (補助動作型): 手番中に攻撃とは別に行える補助魔法や練技など。is_minor: true の行動として登録します。
   - △ (戦闘準備型): 戦闘開始時に発動する能力。多くは補助動作型に準じます。
   - 💬 (宣言型): 近接攻撃等と同時に宣言する特技（魔力撃、全力攻撃など）。

2. **抵抗・達成値・ダメージの表記 `能力名／X (Y)／対抗基準／対抗結果`**:
   - X: 能動側の判定基準値（2d6+X で振る値。use_custom: true, acc: X に設定）。
   - 対抗基準: 回避力、生命抵抗力、精神抵抗力など。
   - 対抗結果:
     - 「半減」かつ魔法ダメージ ➔ type: "魔法(生命抵抗/半減)" または "魔法(精神抵抗/半減)"
     - 「消滅」かつ魔法ダメージ ➔ type: "魔法(生命抵抗/消滅)" または "魔法(精神抵抗/消滅)"
     - ダメージを伴わないデバフ・状態異常 ➔ type: "効果付与のみ(生命抵抗/消滅)" など
   - ダメージ計算: 「2d+25」のように記載されている場合、固定加算値の 25 を dmg に設定します。
   - 射程・範囲の指定 (range_pref):
     - 近接攻撃・前衛優先 ➔ range_pref: "前衛優先", tgt_count: 1
     - 後衛優先 ➔ range_pref: "後衛優先", tgt_count: 1
     - 射撃・単体魔法（敵を自由に選択可） ➔ range_pref: "すべての敵を攻撃可能", tgt_count: 1
     - 前衛エリア全体攻撃（薙ぎ払い、尾撃など） ➔ range_pref: "前衛エリア全体", tgt_count: 0
     - 後衛エリア全体攻撃 ➔ range_pref: "後衛エリア全体", tgt_count: 0
     - 全エリア全体攻撃（ブレス、ファイヤーボール、空間全体など） ➔ range_pref: "全エリア全体(戦場のすべての敵)", tgt_count: 0

3. **発動条件（exec_cond）の指定**:
   - 後衛キャラクターの近接武器攻撃（自陣前衛が全滅するまで発動を控える場合）:
     ➔ exec_cond: "自陣前衛が全滅時のみ (後衛の近接など)"
   - 前衛が健在のときのみ使える行動:
     ➔ exec_cond: "自陣前衛が生存中のみ"
   - ピンチ時にのみ発動する逆境スキル・暴走など:
     ➔ exec_cond: "自身HP50%以下時のみ (ピンチ時)"
   - それ以外の通常行動・魔法・射撃:
     ➔ exec_cond: "常時 (制限なし)"

4. **回復行動の思考ルーチン**:
   - 回復魔法（自身/味方）は味方に負傷者（残HP割合 <= heal_th）が存在しMPがある場合、**他の主動作よりも最優先**で実行されます。全員全快時やMP不足時は自動的に他の行動を行います。

5. **追加効果（バフ/デバフ/毒など・2種類まで設定可能）**:
   - 行動ごとに「effects」配列で最大2種類以上の追加効果を設定できます。
   - target: "自身" または "攻撃対象"
   - stat: "命中力", "回避力", "打撃点", "防護点", "生命抵抗力", "精神抵抗力", "毎ターンHP減少(毒)", "毎ターンMP減少"
   - amount: 変化量（例: 全力攻撃の自身回避デバフなら -2、毒なら毎ターンHP減少量）
   - dur: 効果持続ターン数（例: 1, 3。0で永続）

6. **部位連動・特殊ギミックの再現（飛翔・かばう・部位固有の独立再生）**:
   - ◯飛翔: 「翼のいずれかがHP0以下で失われる。近接攻撃の命中・回避に+1」
     ➔ 連動する部位（胴体や翼など）に dep_name: "翼", dep_acc: 1, dep_eva: 1 を設定。
     （※同名部位が複数ある場合、部位名 name は「翼」のままで指定して構いません。シミュレーターが「翼A」「翼B」と自動連番化し、dep_name: "翼" でいずれかの翼が生存している限りボーナスが有効になります）
   - 前提部位が生きていないと狙えない部位（庇う、胴体内部の核など）:
     ➔ dep_name: "前提部位名", dep_protect: true を設定。
   - ◯再生・独立リジェネ・MP自動回復:
     ➔ upkeep_hp: -回復量（マイナス値。例: ◯再生=5点なら -5、毎ターン自傷ダメージなら +5）。
     ➔ upkeep_mp: -回復量（マイナス値。例: MP自動回復なら -3、毎ターン消費なら +3）。
     ※【重要】upkeep_hp / upkeep_mp は部位ごとに完全独立して動作します。前提部位への依存関係は不要です。他の部位に依存しないその部位固有の再生能力の場合は、dep_name は空文字 "" のままで upkeep_hp を設定してください。

7. **主動作の実行優先順位（回復最優先 ＞ 特定対象狙い ＞ 使用間隔・クールダウン優先）**:
   - 優先順位1: 味方に負傷者がいる場合の回復魔法（自身/味方）。
   - 優先順位2: **特定の対象（キャラまたは部位）が指定された行動（`specific_target`）**。指定した部位・キャラクターが対象陣営に存在し生存している場合、最優先（回復の次）でその部位へ実行されます（複数ある場合は使用間隔 `cooldown` が長い順）。指定部位が存在しない、または撃破済みの場合は実行されません。
   - 優先順位3: 通常の定期行動（`specific_target` なし）。同一ターンに使用可能なものが複数ある場合、シミュレーターは**使用間隔（`cooldown`: 0〜18）が長い大技を優先して実行**します（同値なら登録順）。
   - 優先順位4: 基本行動（通常攻撃）。
   - **開始ターン（start_turn）**: 戦闘開始から何ターン目に解禁されるかを指定できます（通常は1。例えば3ターン目以降に解禁される形態変化や必殺技なら3）。

---

### 【出力仕様・JSONフォーマット】

出力は必ず ```json ... ``` のコードブロックのみとし、前後に余計な解説文を含めないでください。

```json
[
  {
    "char_name": "キャラクター・魔物の名称（例：ドレイクカウント(竜形態)）",
    "position": "前衛",
    "strategy": "コア部位優先",
    "fixed": false,
    "parts": [
      {
        "name": "部位名（例：牙(胴体)、翼(左) など）",
        "is_core": true,
        "hp": 165,
        "mp": 90,
        "acc": 23,
        "eva": 22,
        "dmg": 22,
        "dfn": 22,
        "fort": 24,
        "will": 24,
        "mag": 21,
        "mag_dmg": 21,
        "dep_name": "",
        "dep_acc": 0,
        "dep_eva": 0,
        "dep_protect": false,
        "upkeep_hp": 0,
        "upkeep_mp": 0,
        "b_act": {
          "name": "通常攻撃（例：牙、武器攻撃）",
          "type": "物理攻撃",
          "mp": 0,
          "mp_type": "1回固定",
          "is_minor": false,
          "use_custom": false,
          "acc": 0,
          "dmg": 0,
          "tgt_side": "敵",
          "range_pref": "前衛優先",
          "tgt_count": 1,
          "heal_th": 100,
          "exec_cond": "常時 (制限なし)",
          "specific_target": "",
          "effects": []
        },
        "sp_acts": [
          {
            "name": "特殊能力名（例：燦光のブレス、魔力撃など）",
            "type": "魔法(生命抵抗/半減)",
            "mp": 0,
            "mp_type": "1回固定",
            "cooldown": 2,
            "start_turn": 1,
            "is_minor": false,
            "use_custom": true,
            "acc": 24,
            "dmg": 25,
            "tgt_side": "敵",
            "range_pref": "全エリア全体(戦場のすべての敵)",
            "tgt_count": 0,
            "heal_th": 100,
            "exec_cond": "常時 (制限なし)",
            "specific_target": "",
            "effects": [
              {
                "target": "自身",
                "stat": "回避力",
                "amount": -2,
                "dur": 1
              },
              {
                "target": "自身",
                "stat": "精神抵抗力",
                "amount": -2,
                "dur": 1
              }
            ]
          }
        ]
      }
    ]
  }
]
```"""

def styled_header(text, rgba_bg, rgba_border):
    st.markdown(f"""
    <div style="background-color: {rgba_bg}; border-left: 4px solid {rgba_border}; padding: 5px 10px; margin-top: 10px; margin-bottom: 12px; border-radius: 3px;">
        <span style="font-weight: bold; font-size: 0.95em;">{text}</span>
    </div>
    """, unsafe_allow_html=True)

def render_effects_editor(act_dict, prefix_key):
    if 'effects' not in act_dict: act_dict['effects'] = []

    with st.container(border=True):
        styled_header("🟪 追加効果 (バフ/デバフ/毒など・2種類まで設定可能)", "rgba(156, 39, 176, 0.15)", "rgba(156, 39, 176, 0.6)")

        stat_opts = ["命中力", "回避力", "打撃点", "防護点", "生命抵抗力", "精神抵抗力", "毎ターンHP減少(毒)", "毎ターンMP減少"]

        for k, eff in enumerate(act_dict['effects']):
            st.markdown(f"**【追加効果 {k+1}】**")
            c7, c8, c9, c10, c11 = st.columns([1.5, 2.5, 2, 2, 0.8])
            with c7:
                cur_tgt = eff.get('target', '攻撃対象')
                if cur_tgt not in ["自身", "攻撃対象"]: cur_tgt = "攻撃対象"
                eff['target'] = st.selectbox("対象", ["自身", "攻撃対象"], index=["自身", "攻撃対象"].index(cur_tgt), key=f"{prefix_key}_eft_{k}")
            with c8:
                cur_stat = eff.get('stat', '命中力')
                if cur_stat not in stat_opts: cur_stat = "命中力"
                eff['stat'] = st.selectbox("ステータス", stat_opts, index=stat_opts.index(cur_stat), key=f"{prefix_key}_efs_{k}")
            with c9:
                eff['amount'] = int(st.number_input("変化量", -100, 100, eff.get('amount', 0), step=1, key=f"{prefix_key}_efa_{k}") or 0)
            with c10:
                eff['dur'] = int(st.number_input("ターン", 0, 100, eff.get('dur', 3), step=1, key=f"{prefix_key}_efd_{k}", help="0で永続") or 0)
            with c11:
                st.write("")
                if st.button("❌", key=f"{prefix_key}_efdel_{k}", help="この効果を削除"):
                    act_dict['effects'].pop(k)
                    st.rerun()

        btn_label = "➕ 2つ目の効果を追加" if len(act_dict['effects']) == 1 else "➕ 効果を追加"
        if st.button(btn_label, key=f"{prefix_key}_efadd"):
            new_stat = "回避力" if len(act_dict['effects']) == 1 else "命中力"
            act_dict['effects'].append({"target": "攻撃対象", "stat": new_stat, "amount": -2, "dur": 3})
            st.rerun()

def render_action_editor(act_dict, prefix_key, is_special=False):
    c1, c2, c3, c4 = st.columns([2.5, 2.5, 1.8, 1.8])
    with c1: act_dict['name'] = st.text_input("行動名", act_dict.get('name', '攻撃'), key=f"{prefix_key}_name")
    with c2: act_dict['type'] = st.selectbox("タイプ", act_options, index=act_options.index(act_dict.get('type', '物理攻撃')), key=f"{prefix_key}_type")
    with c3: act_dict['mp'] = int(st.number_input("消費MP", 0, 100, act_dict.get('mp', 0), step=1, key=f"{prefix_key}_mp") or 0)
    with c4: act_dict['mp_type'] = st.selectbox("MP計算", ["1回固定", "対象数に比例"], index=["1回固定", "対象数に比例"].index(act_dict.get('mp_type', '1回固定')), key=f"{prefix_key}_mpty")

    c_min, c_cond = st.columns([1.8, 4.2])
    with c_min:
        st.write("")
        act_dict['is_minor'] = st.checkbox("補助動作", value=act_dict.get('is_minor', False), key=f"{prefix_key}_ismin", help="1ターンに何度でも(主動作とも同時に)行えます")
    with c_cond:
        exec_cond_opts = [
            "常時 (制限なし)",
            "自陣前衛が全滅時のみ (後衛の近接など)",
            "自陣前衛が生存中のみ",
            "自身HP50%以下時のみ (ピンチ時)"
        ]
        cur_cond = act_dict.get('exec_cond', exec_cond_opts[0])
        if cur_cond not in exec_cond_opts: cur_cond = exec_cond_opts[0]
        act_dict['exec_cond'] = st.selectbox("発動条件", exec_cond_opts, index=exec_cond_opts.index(cur_cond), key=f"{prefix_key}_econd", help="条件を満たしている場合のみこの行動を実行します（後衛にいる時の近接攻撃制限などに便利です）")

    if is_special:
        c_cd1, c_cd2 = st.columns(2)
        with c_cd1:
            act_dict['cooldown'] = int(st.number_input("使用間隔 (何ターンに1回か。0で毎ターン)", 0, 18, act_dict.get('cooldown', 2), step=1, key=f"{prefix_key}_cd") or 0)
        with c_cd2:
            act_dict['start_turn'] = int(st.number_input("開始ターン (何ターン目から実行可能か)", 1, 100, act_dict.get('start_turn', 1), step=1, key=f"{prefix_key}_sturn", help="1で最初(1ターン目)から使用可能。例えば3に設定すると3ターン目以降に解禁されます") or 1)

    with st.container(border=True):
        styled_header("🎯 対象・属性設定", "rgba(33, 150, 243, 0.15)", "rgba(33, 150, 243, 0.6)")
        all_range_opts = ["前衛優先", "後衛優先", "すべての敵を攻撃可能", "前衛エリア全体", "後衛エリア全体", "全エリア全体(戦場のすべての敵)"]
        heal_range_opts = ["対象自由(味方)", "前衛エリア全体", "後衛エリア全体", "全エリア全体(味方全員)"]

        if "回復" in act_dict.get('type', ''):
            c_t1, c_t2, c_t3, c_t4 = st.columns([1.5, 2.5, 2, 2])
            with c_t1: act_dict['tgt_side'] = st.selectbox("対象陣営", ["味方", "自身"], index=["味方", "自身"].index(act_dict.get('tgt_side', '味方') if act_dict.get('tgt_side') in ["味方", "自身"] else '味方'), key=f"{prefix_key}_tgts")
            with c_t2:
                cur_r = act_dict.get('range_pref', '対象自由(味方)')
                if cur_r not in heal_range_opts: cur_r = heal_range_opts[0]
                act_dict['range_pref'] = st.selectbox("射程/範囲", heal_range_opts, index=heal_range_opts.index(cur_r), key=f"{prefix_key}_rpref")
            is_area = "全体" in act_dict['range_pref']
            with c_t3:
                if is_area:
                    st.text_input("同時対象数", value="エリア全員 (全体)", disabled=True, key=f"{prefix_key}_tgtc_dis")
                    act_dict['tgt_count'] = 0
                else:
                    act_dict['tgt_count'] = int(st.number_input("同時対象数 (0=全体)", 0, 10, act_dict.get('tgt_count', 1), step=1, key=f"{prefix_key}_tgtc") or 0)
            with c_t4: act_dict['heal_th'] = int(st.number_input("回復判断(HP%以下)", 1, 100, act_dict.get('heal_th', 100), step=1, key=f"{prefix_key}_hth") or 100)
        else:
            c_t1, c_t2, c_t3 = st.columns([1.5, 2.5, 2])
            with c_t1: act_dict['tgt_side'] = st.selectbox("対象陣営", ["敵", "味方", "自身"], index=["敵", "味方", "自身"].index(act_dict.get('tgt_side', '敵')), key=f"{prefix_key}_tgts")
            with c_t2:
                cur_r = act_dict.get('range_pref', '前衛優先')
                if cur_r not in all_range_opts: cur_r = '前衛優先'
                act_dict['range_pref'] = st.selectbox("射程/範囲", all_range_opts, index=all_range_opts.index(cur_r), key=f"{prefix_key}_rpref")
            is_area = "全体" in act_dict['range_pref']
            with c_t3:
                if is_area:
                    st.text_input("同時対象数", value="エリア全員 (全体)", disabled=True, key=f"{prefix_key}_tgtc_dis")
                    act_dict['tgt_count'] = 0
                else:
                    act_dict['tgt_count'] = int(st.number_input("同時対象数 (0=全体)", 0, 10, act_dict.get('tgt_count', 1), step=1, key=f"{prefix_key}_tgtc") or 0)
            act_dict['heal_th'] = 100

        # 特定の対象（キャラまたは部位）の指定
        is_pc = prefix_key.startswith("pc")
        cur_tgt_side = act_dict.get('tgt_side', '敵')
        if cur_tgt_side == "敵":
            cand_roster = st.session_state.get('draft_en_roster', []) if is_pc else st.session_state.get('draft_pc_roster', [])
        elif cur_tgt_side == "味方":
            cand_roster = st.session_state.get('draft_pc_roster', []) if is_pc else st.session_state.get('draft_en_roster', [])
        else:
            cand_roster = []

        spec_options = ["(指定なし / 自動選定)"]
        opt_value_map = {"(指定なし / 自動選定)": ""}

        seen_parts = set()
        for cand_c in cand_roster:
            cname = cand_c.get('char_name', '')
            if cname:
                lbl_c = f"[キャラ] {cname}"
                spec_options.append(lbl_c)
                opt_value_map[lbl_c] = cname
            numbered_cand_parts = get_numbered_part_names(cand_c.get('parts', []))
            for cand_p, pname in zip(cand_c.get('parts', []), numbered_cand_parts):
                if pname:
                    if cname:
                        lbl_cp = f"[部位] {cname} - {pname}"
                        spec_options.append(lbl_cp)
                        opt_value_map[lbl_cp] = f"{cname} - {pname}"
                    seen_parts.add(pname)
                    orig_pname = cand_p.get('name', '')
                    if orig_pname:
                        seen_parts.add(orig_pname)

        for pname in sorted(seen_parts):
            lbl_p = f"[部位名のみ] {pname}"
            if lbl_p not in spec_options:
                spec_options.append(lbl_p)
                opt_value_map[lbl_p] = pname

        spec_options.append("✏️ (直接入力)")

        cur_saved = act_dict.get('specific_target', '').strip()
        match_label = None
        for lbl, val in opt_value_map.items():
            if val == cur_saved and cur_saved != "":
                match_label = lbl
                break

        if not cur_saved:
            default_idx = 0
        elif match_label and match_label in spec_options:
            default_idx = spec_options.index(match_label)
        else:
            default_idx = spec_options.index("✏️ (直接入力)")

        c_spc1, c_spc2 = st.columns([1.5, 1.5])
        with c_spc1:
            sel_choice = st.selectbox(
                "特定の対象を選択 (キャラ/部位)",
                spec_options,
                index=default_idx,
                key=f"{prefix_key}_spc_sel",
                help="対象陣営に存在する特定のキャラクターや部位をプルダウンから選択します。設定すると、その部位が生存中のみ最優先(回復の次)で狙います。存在しない/撃破済みの場合は行動しません。"
            )
        with c_spc2:
            if sel_choice == "✏️ (直接入力)":
                custom_init = cur_saved if cur_saved not in opt_value_map.values() else ""
                act_dict['specific_target'] = st.text_input(
                    "対象名 (直接入力)",
                    value=custom_init,
                    key=f"{prefix_key}_spc_txt",
                    placeholder="例: 翼, 牙(胴体), キャラ名",
                    help="直接入力した部位やキャラが生存している場合のみ実行されます"
                )
            else:
                act_dict['specific_target'] = opt_value_map.get(sel_choice, "")
                st.text_input("指定中の対象", value=act_dict['specific_target'] or "(指定なし / 自動選定)", disabled=True, key=f"{prefix_key}_spc_disp")

        act_dict['use_custom'] = st.checkbox("独自の達成値・ダメージを使う", value=act_dict.get('use_custom', False), key=f"{prefix_key}_uc")
        if act_dict['use_custom']:
            c4, c5 = st.columns(2)
            with c4: act_dict['acc'] = int(st.number_input("達成値(命中/魔力) 基準", 0, 100, act_dict.get('acc', 0), step=1, key=f"{prefix_key}_acc") or 0)
            with c5: act_dict['dmg'] = int(st.number_input("ダメージ/回復 基準", 0, 100, act_dict.get('dmg', 0), step=1, key=f"{prefix_key}_dmg") or 0)

    render_effects_editor(act_dict, prefix_key)

def render_status_table(draft_parts, prefix):
    st.write("▼ ステータス (一覧表で直接編集・行追加・削除ができます)")
    numbered_names = get_numbered_part_names(draft_parts)
    data = []
    for p, disp_name in zip(draft_parts, numbered_names):
        data.append({
            "コア": p.get('is_core', False),
            "部位名": disp_name,
            "命中力": p.get('acc', 0),
            "打撃点": p.get('dmg', 0),
            "回避力": p.get('eva', 0),
            "防護点": p.get('dfn', 0),
            "HP": p.get('hp', 1),
            "MP": p.get('mp', 0),
            "生命抵抗": p.get('fort', 0),
            "精神抵抗": p.get('will', 0),
            "魔力": p.get('mag', 0),
            "魔法D": p.get('mag_dmg', 0),
        })

    column_config = {
        "コア": st.column_config.CheckboxColumn("コア", default=False),
        "部位名": st.column_config.TextColumn("部位名", required=True, default="無名"),
        "命中力": st.column_config.NumberColumn("命中力", step=1, default=0),
        "打撃点": st.column_config.NumberColumn("打撃点", step=1, default=0),
        "回避力": st.column_config.NumberColumn("回避力", step=1, default=0),
        "防護点": st.column_config.NumberColumn("防護点", step=1, default=0),
        "HP": st.column_config.NumberColumn("HP", step=1, default=10),
        "MP": st.column_config.NumberColumn("MP", step=1, default=0),
        "生命抵抗": st.column_config.NumberColumn("生命抵抗", step=1, default=0),
        "精神抵抗": st.column_config.NumberColumn("精神抵抗", step=1, default=0),
        "魔力": st.column_config.NumberColumn("魔力", step=1, default=0),
        "魔法D": st.column_config.NumberColumn("魔法D", step=1, default=0),
    }

    edited_data = st.data_editor(data, num_rows="dynamic", key=f"{prefix}_status_table", use_container_width=True, column_config=column_config)

    new_parts = []
    for i, row in enumerate(edited_data):
        if i < len(draft_parts): p = draft_parts[i]
        else: p = {"id": str(uuid.uuid4()), "b_act": {}, "sp_acts": []}

        p['is_core'] = bool(row.get('コア', False))

        edited_name = str(row.get('部位名') or '無名').strip()
        if i < len(numbered_names) and edited_name == numbered_names[i]:
            pass  # 連番付き表示のまま変更されていない場合は元の名称(例: "翼")を維持
        else:
            p['name'] = edited_name

        p['acc'] = int(row.get('命中力') or 0)
        p['dmg'] = int(row.get('打撃点') or 0)
        p['eva'] = int(row.get('回避力') or 0)
        p['dfn'] = int(row.get('防護点') or 0)
        p['hp'] = max(1, int(row.get('HP') or 1))
        p['mp'] = int(row.get('MP') or 0)
        p['fort'] = int(row.get('生命抵抗') or 0)
        p['will'] = int(row.get('精神抵抗') or 0)
        p['mag'] = int(row.get('魔力') or 0)
        p['mag_dmg'] = int(row.get('魔法D') or 0)
        new_parts.append(p)

    draft_parts.clear()
    draft_parts.extend(new_parts)


def render_single_part_actions(part: dict, pname: str, prefix: str, is_multi_part: bool = True, show_upkeep: bool = False):
    uid = part['id']
    if 'b_act' not in part or not isinstance(part['b_act'], dict):
        part['b_act'] = {
            "name": "通常攻撃", "type": "物理攻撃", "mp": 0, "mp_type": "1回固定",
            "use_custom": False, "acc": 0, "dmg": 0, "effects": [],
            "tgt_side": "敵", "range_pref": "前衛優先", "tgt_count": 1, "heal_th": 100,
            "exec_cond": "常時 (制限なし)", "specific_target": ""
        }
    b_act = part['b_act']
    b_name = b_act.get('name', '通常攻撃') or '通常攻撃'
    b_label = f"🟥 基本: {b_name}"
    if len(b_label) > 13: b_label = b_label[:12] + "…"

    sp_acts = part.setdefault('sp_acts', [])
    sp_labels = []
    for sp_idx, sp in enumerate(sp_acts):
        sp_name = sp.get('name', f'特殊{sp_idx+1}') or f'特殊{sp_idx+1}'
        sp_lbl = f"🟩 特殊: {sp_name}"
        if len(sp_lbl) > 13: sp_lbl = sp_lbl[:12] + "…"
        sp_labels.append(sp_lbl)

    if show_upkeep:
        all_labels = [b_label] + sp_labels + ["🔗 リジェネ・詳細", "➕ 行動追加"]
    else:
        all_labels = [b_label] + sp_labels + ["➕ 行動追加"]

    act_tabs = st.tabs(all_labels)

    # 1. 基本行動
    with act_tabs[0]:
        styled_header(f"🟥 基本行動 (毎ターンの主力行動) : {b_name}", "rgba(255, 82, 82, 0.15)", "rgba(255, 82, 82, 0.6)")
        render_action_editor(b_act, f"{prefix}_bact_{uid}", is_special=False)

    # 2. 定期行動
    for sp_idx, sp in enumerate(sp_acts):
        with act_tabs[1 + sp_idx]:
            sp_name = sp.get('name', f'特殊{sp_idx+1}') or f'特殊{sp_idx+1}'
            styled_header(f"🟩 定期行動 : {sp_name}", "rgba(76, 175, 80, 0.15)", "rgba(76, 175, 80, 0.6)")
            render_action_editor(sp, f"{prefix}_sp_{uid}_{sp_idx}", is_special=True)

            c_sp_b1, c_sp_b2 = st.columns(2)
            with c_sp_b1:
                if st.button("👥 この行動を複製", key=f"{prefix}_sp_dup_{uid}_{sp_idx}", use_container_width=True):
                    new_sp = copy.deepcopy(sp)
                    if not new_sp.get('name', '').endswith("(コピー)"):
                        new_sp['name'] = f"{new_sp.get('name', '特殊行動')} (コピー)"
                    sp_acts.insert(sp_idx + 1, new_sp)
                    st.rerun()
            with c_sp_b2:
                if st.button("🗑️ この行動を削除", key=f"{prefix}_sp_del_{uid}_{sp_idx}", use_container_width=True):
                    sp_acts.pop(sp_idx)
                    st.rerun()

    # 3. リジェネ・詳細 (単一部位の場合のみ)
    if show_upkeep:
        with act_tabs[1 + len(sp_acts)]:
            styled_header("🔗 ターン開始時効果 (リジェネ・MP回復)", "rgba(255, 152, 0, 0.15)", "rgba(255, 152, 0, 0.6)")
            c5, c6 = st.columns(2)
            with c5: part['upkeep_hp'] = int(st.number_input("ターン開始時HP増減 (マイナスでリジェネ)", -100, 100, part.get('upkeep_hp', 0), step=1, key=f"{prefix}_uphp_{uid}") or 0)
            with c6: part['upkeep_mp'] = int(st.number_input("ターン開始時MP増減", -100, 100, part.get('upkeep_mp', 0), step=1, key=f"{prefix}_upmp_{uid}") or 0)

    # 4. 新規行動追加タブ
    with act_tabs[-1]:
        styled_header(f"➕ 【{pname}】に新しい定期行動を追加", "rgba(33, 150, 243, 0.15)", "rgba(33, 150, 243, 0.6)")
        new_act_name = st.text_input("行動の名称", value="特殊能力", key=f"{prefix}_new_act_name_{uid}")

        if st.button("➕ この行動を追加する", key=f"{prefix}_btn_add_sp_{uid}", type="primary", use_container_width=True):
            sp_acts.append({
                "name": new_act_name.strip() or "特殊能力",
                "type": "魔法(生命抵抗/半減)",
                "mp": 0,
                "mp_type": "1回固定",
                "cooldown": 2,
                "start_turn": 1,
                "is_minor": False,
                "use_custom": True,
                "acc": 10,
                "dmg": 10,
                "effects": [],
                "tgt_side": "敵",
                "range_pref": "前衛優先",
                "tgt_count": 1,
                "heal_th": 100,
                "exec_cond": "常時 (制限なし)",
                "specific_target": ""
            })
            st.rerun()


def render_part_relations_tab(draft_parts: List[dict], numbered_pnames: List[str], prefix: str):
    styled_header("🔗 特殊能力・部位連動 & ターン開始時効果", "rgba(255, 152, 0, 0.15)", "rgba(255, 152, 0, 0.6)")
    for p_idx, (part, pname) in enumerate(zip(draft_parts, numbered_pnames)):
        uid = part['id']
        st.markdown(f"**【部位: {pname}】**")

        # 前提部位の選択肢を構築 (そのキャラの他の部位から自動連番付きで取得)
        dep_options = ["(連動なし)"]
        dep_val_map = {"(連動なし)": ""}

        other_numbered = [name for k, name in enumerate(numbered_pnames) if k != p_idx]
        other_base = [draft_parts[k].get('name', '部位') for k, name in enumerate(numbered_pnames) if k != p_idx]

        for oname in other_numbered:
            dep_options.append(oname)
            dep_val_map[oname] = oname

        # 同名部位が複数ある場合 (例: 翼A, 翼B)、いずれかの生存を判定するベース名オプションも提供
        base_counts = {}
        for bname in other_base:
            if bname:
                base_counts[bname] = base_counts.get(bname, 0) + 1
        for bname, cnt in base_counts.items():
            if cnt > 1:
                lbl_any = f"{bname} (いずれかの{bname}が生存)"
                dep_options.append(lbl_any)
                dep_val_map[lbl_any] = bname

        dep_options.append("✏️ (直接入力)")

        cur_dep = part.get('dep_name', '').strip()
        match_label = None
        for lbl, val in dep_val_map.items():
            if val == cur_dep and cur_dep != "":
                match_label = lbl
                break

        if not cur_dep:
            default_idx = 0
        elif match_label and match_label in dep_options:
            default_idx = dep_options.index(match_label)
        else:
            default_idx = dep_options.index("✏️ (直接入力)")

        c_dp1, c_dp2, c_dp3, c_dp4 = st.columns([1.8, 1.1, 1.1, 2.0])
        with c_dp1:
            sel_dep = st.selectbox(
                "前提部位名",
                dep_options,
                index=default_idx,
                key=f"{prefix}_dpn_sel_{uid}",
                help="連動条件となる部位を他の部位から選択します。（飛翔の翼、胴体による保護など）"
            )
            if sel_dep == "✏️ (直接入力)":
                custom_init = cur_dep if cur_dep not in dep_val_map.values() else ""
                part['dep_name'] = st.text_input(
                    "前提部位名 (直接入力)",
                    value=custom_init,
                    key=f"{prefix}_dpn_txt_{uid}",
                    placeholder="例: 翼, 胴体"
                ).strip()
            else:
                part['dep_name'] = dep_val_map[sel_dep]

        with c_dp2:
            part['dep_acc'] = int(st.number_input("命中ボーナス", -10, 20, part.get('dep_acc', 0), step=1, key=f"{prefix}_dpa_{uid}") or 0)
        with c_dp3:
            part['dep_eva'] = int(st.number_input("回避ボーナス", -10, 20, part.get('dep_eva', 0), step=1, key=f"{prefix}_dpe_{uid}") or 0)
        with c_dp4:
            st.write("")
            part['dep_protect'] = st.checkbox("前提部位の生存中は攻撃対象不可", value=part.get('dep_protect', False), key=f"{prefix}_dpp_{uid}", help="前提部位のHPが残っている間、この部位は敵の攻撃対象に選ばれません")

        c5, c6 = st.columns(2)
        with c5: part['upkeep_hp'] = int(st.number_input("ターン開始時HP増減 (マイナスでリジェネ)", -100, 100, part.get('upkeep_hp', 0), step=1, key=f"{prefix}_uphp_{uid}") or 0)
        with c6: part['upkeep_mp'] = int(st.number_input("ターン開始時MP増減", -100, 100, part.get('upkeep_mp', 0), step=1, key=f"{prefix}_upmp_{uid}") or 0)

        if st.button("👥 この部位全体を複製", key=f"{prefix}_dup_{uid}", use_container_width=True):
            new_part = copy.deepcopy(part)
            new_part['id'] = str(uuid.uuid4())
            new_part['name'] = f"{new_part.get('name', '部位')} (コピー)"
            draft_parts.append(new_part)
            st.rerun()
        if p_idx < len(draft_parts) - 1:
            st.markdown("---")


def render_actions_editor(draft_parts: List[dict], prefix: str):
    st.write("▼ 部位・行動の設定")
    if not draft_parts:
        st.info("部位が存在しません。上のステータス表で行を追加してください。")
        return

    numbered_pnames = get_numbered_part_names(draft_parts)
    is_multi_part = (len(draft_parts) > 1)

    if is_multi_part:
        # --- 多部位キャラクター: 部位切り替えタブ (Level 1) ---
        part_tab_labels = []
        for p, pname in zip(draft_parts, numbered_pnames):
            core_icon = "👑 " if p.get('is_core', False) else "🔹 "
            part_tab_labels.append(f"{core_icon}{pname}")
        part_tab_labels.append("🔗 部位連動・詳細")

        part_tabs = st.tabs(part_tab_labels)

        for p_idx, (part, pname) in enumerate(zip(draft_parts, numbered_pnames)):
            with part_tabs[p_idx]:
                render_single_part_actions(part, pname, f"{prefix}_p{p_idx}", is_multi_part=True, show_upkeep=False)

        with part_tabs[-1]:
            render_part_relations_tab(draft_parts, numbered_pnames, prefix)
    else:
        # --- 単一部位キャラクター: 直接その部位の行動タブを表示 ---
        part = draft_parts[0]
        pname = numbered_pnames[0]
        render_single_part_actions(part, pname, f"{prefix}_p0", is_multi_part=False, show_upkeep=True)

def render_single_char_editor(c: dict, i: int, roster: List[dict], side_prefix: str, side_label: str):
    if "char_id" not in c or not c["char_id"]:
        c["char_id"] = str(uuid.uuid4())
    cid = c["char_id"]

    c_header1, c_header_order, c_header2, c_header3 = st.columns([1.6, 1.0, 0.8, 0.8])
    with c_header1:
        c["char_name"] = st.text_input("キャラ名", c.get("char_name", ""), key=f"{side_prefix}_name_{cid}")
    with c_header_order:
        st.write("")
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            if st.button("◀", key=f"{side_prefix}_mvl_{cid}", disabled=(i == 0), help="タブの並び順を左へ（前に）移動"):
                roster[i], roster[i - 1] = roster[i - 1], roster[i]
                target_base = 0 if side_prefix == "pc" else len(st.session_state.draft_pc_roster) + 1
                st.session_state.target_tab_click = target_base + (i - 1)
                st.rerun()
        with col_m2:
            if st.button("▶", key=f"{side_prefix}_mvr_{cid}", disabled=(i >= len(roster) - 1), help="タブの並び順を右へ（後に）移動"):
                roster[i], roster[i + 1] = roster[i + 1], roster[i]
                target_base = 0 if side_prefix == "pc" else len(st.session_state.draft_pc_roster) + 1
                st.session_state.target_tab_click = target_base + (i + 1)
                st.rerun()
    with c_header2:
        st.write("")
        if st.button("👥 複製", key=f"{side_prefix}_dup_{cid}"):
            new_char = copy.deepcopy(c)
            new_char["char_id"] = str(uuid.uuid4())
            new_char["char_name"] = new_char["char_name"] + " (コピー)"
            for p in new_char["parts"]: p["id"] = str(uuid.uuid4())
            roster.append(new_char)
            st.rerun()
    with c_header3:
        st.write("")
        if len(roster) > 1:
            if st.button("🗑️ 削除", key=f"{side_prefix}_del_{cid}"):
                cleanup_char_keys(c)
                roster.pop(i)
                st.rerun()
        else:
            st.button("🗑️ 不可", disabled=True, key=f"{side_prefix}_del_{cid}", help="陣営には最低1体のキャラクターが必要です")

    with st.expander("💾 このキャラの保存 / 読込", expanded=False):
        char_sc1, char_sc2 = st.columns(2)
        with char_sc1:
            c_name_safe = c.get('char_name', 'char') or 'char'
            st.download_button("📥 このキャラを保存 (DL)", data=lambda c=c: json.dumps(c, ensure_ascii=False, indent=2), file_name=f"{c_name_safe}.json", mime="application/json", key=f"{side_prefix}_char_dl_{cid}")
        with char_sc2:
            char_up = st.file_uploader("📂 このキャラに上書読込", type=["json"], key=f"{side_prefix}_char_ul_{cid}", label_visibility="collapsed")
            if char_up and st.button("上書反映", key=f"{side_prefix}_char_load_btn_{cid}"):
                if load_single_char_data(char_up, c):
                    st.rerun()

    c1, c2, c3 = st.columns([1.5, 1.5, 1])
    with c1:
        pos_opts = ["前衛", "後衛"]
        cur_pos = c.get("position", "前衛")
        if cur_pos not in pos_opts: cur_pos = "前衛"
        c["position"] = st.selectbox("配置", pos_opts, index=pos_opts.index(cur_pos), key=f"{side_prefix}_pos_{cid}")
    with c2: c["strategy"] = st.selectbox("攻撃方針", ["ランダム", "コア部位優先", "倒しやすい部位優先"], index=["ランダム", "コア部位優先", "倒しやすい部位優先"].index(c.get("strategy", "ランダム")), key=f"{side_prefix}_strat_{cid}")
    with c3:
        st.write("")
        c["fixed"] = st.checkbox("固定値(7)で判定する", value=c.get("fixed", False), key=f"{side_prefix}_fix_{cid}")

    cur_mp_beh = c.get("mp_empty_behavior", MP_EMPTY_BEHAVIOR_OPTS[0])
    if cur_mp_beh not in MP_EMPTY_BEHAVIOR_OPTS:
        cur_mp_beh = MP_EMPTY_BEHAVIOR_OPTS[0]
    c["mp_empty_behavior"] = st.selectbox(
        "MP切れ時の挙動 (後衛配置時)",
        MP_EMPTY_BEHAVIOR_OPTS,
        index=MP_EMPTY_BEHAVIOR_OPTS.index(cur_mp_beh),
        key=f"{side_prefix}_mpbeh_{cid}",
        disabled=(c["position"] != "後衛"),
        help="後衛キャラクターがMP切れ（またはMP不足）で行動できなくなった際、前衛へ移動して近接攻撃（『自陣前衛が全滅時のみ』の行動を含む）に参加するか、そのまま後衛にとどまり前衛が倒れるまで待機するかを設定します。"
    )

    render_status_table(c["parts"], f"{side_prefix}_parts_{cid}")
    render_actions_editor(c["parts"], f"{side_prefix}_parts_{cid}")

@st.fragment
def render_combined_sidebar(pc_roster: List[dict], en_roster: List[dict]):
    def get_numbered_char_names(roster: List[dict]) -> List[str]:
        counts = {}
        for c in roster:
            name = c.get('char_name', 'キャラ') or 'キャラ'
            counts[name] = counts.get(name, 0) + 1
        trackers = {name: 0 for name, cnt in counts.items() if cnt > 1}
        result = []
        for c in roster:
            name = c.get('char_name', 'キャラ') or 'キャラ'
            if name in trackers:
                suffix = chr(ord('A') + trackers[name])
                if suffix > 'Z': suffix = str(trackers[name] - 25)
                result.append(f"{name}{suffix}")
                trackers[name] += 1
            else:
                result.append(name)
        return result

    # 上下2段のタブタイトル生成
    tab_titles = []
    pc_names = get_numbered_char_names(pc_roster)
    for c, dname in zip(pc_roster, pc_names):
        tab_titles.append(f"🔵 [{c.get('position', '前衛')}] {dname}")
    tab_titles.append("➕ 味方追加")

    en_names = get_numbered_char_names(en_roster)
    for c, dname in zip(en_roster, en_names):
        tab_titles.append(f"🔴 [{c.get('position', '前衛')}] {dname}")
    tab_titles.append("➕ 敵追加")

    pc_total_tabs = len(pc_roster) + 1
    en_total_tabs = len(en_roster) + 1

    # 動的な陣営別タブ幅計算CSSを注入（共通の固定スタイルは main() に集約）
    st.markdown(f"""
    <style>
    /* 上段: 味方サイド (青系, 1段目を100%専有) */
    section[data-testid="stSidebar"] [role="tablist"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *) > :nth-child(-n+{pc_total_tabs}),
    section[data-testid="stSidebar"] .sw25-char-tablist > :nth-child(-n+{pc_total_tabs}) {{
        flex: 0 0 calc(99.9% / {pc_total_tabs}) !important;
        width: calc(99.9% / {pc_total_tabs}) !important;
        max-width: calc(99.9% / {pc_total_tabs}) !important;
        min-width: 0px !important;
        height: 32px !important;
        background-color: rgba(33, 150, 243, 0.14) !important;
        border-bottom: 2.5px solid #2196f3 !important;
        color: #1976d2 !important;
        font-weight: bold !important;
        box-sizing: border-box !important;
        overflow: hidden !important;
    }}
    /* 下段: 敵サイド (赤系, 2段目を100%専有) */
    section[data-testid="stSidebar"] [role="tablist"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *) > :nth-child(n+{pc_total_tabs + 1}),
    section[data-testid="stSidebar"] .sw25-char-tablist > :nth-child(n+{pc_total_tabs + 1}) {{
        flex: 0 0 calc(99.9% / {en_total_tabs}) !important;
        width: calc(99.9% / {en_total_tabs}) !important;
        max-width: calc(99.9% / {en_total_tabs}) !important;
        min-width: 0px !important;
        height: 32px !important;
        background-color: rgba(244, 67, 54, 0.14) !important;
        border-bottom: 2.5px solid #f44336 !important;
        color: #d32f2f !important;
        font-weight: bold !important;
        box-sizing: border-box !important;
        overflow: hidden !important;
    }}
    </style>
    """, unsafe_allow_html=True)

    all_tabs = st.tabs(tab_titles)

    target_tab_to_click = st.session_state.get('target_tab_click', -1)
    if 'target_tab_click' in st.session_state:
        del st.session_state['target_tab_click']

    if target_tab_to_click >= 0:
        click_js = f"""
        <script>
        (function() {{
            function selectTab() {{
                try {{
                    const doc = window.parent.document;
                    if (!doc) return;
                    const topTablist = doc.querySelector('section[data-testid="stSidebar"] .sw25-char-tablist, section[data-testid="stSidebar"] [role="tablist"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *)');
                    if (topTablist) {{
                        const tabs = topTablist.querySelectorAll('[role="tab"]');
                        if (tabs && {target_tab_to_click} < tabs.length) {{
                            tabs[{target_tab_to_click}].click();
                        }}
                    }}
                }} catch (e) {{}}
            }}
            selectTab();
            setTimeout(selectTab, 50);
            setTimeout(selectTab, 150);
            setTimeout(selectTab, 350);
        }})();
        </script>
        """
        st.components.v1.html(click_js, height=0)

    # 1. 味方キャラクター
    for i, c in enumerate(pc_roster):
        with all_tabs[i]:
            render_single_char_editor(c, i, pc_roster, "pc", "PC")

    # 2. 味方追加タブ
    with all_tabs[len(pc_roster)]:
        st.subheader("🔵 味方PCの追加・一括管理")
        col_new_p1, col_new_p2 = st.columns(2)
        with col_new_p1:
            if st.button("➕ 新しいPCを追加", key="add_new_pc_btn", use_container_width=True):
                pc_roster.append(create_default_char("新規PC"))
                st.rerun()
        with col_new_p2:
            if st.button("🔄 デフォルト編成に戻す", key="reset_pc_default_btn", use_container_width=True, help="味方陣営をデフォルトキャラクター（腕利きの傭兵・人族の神官）に戻します"):
                cleanup_roster_keys(pc_roster)
                pc_roster.clear()
                pc_roster.extend(get_default_pc_roster())
                st.rerun()

        st.markdown("---")
        st.write("▼ ファイルからPCを個別追加")
        new_pc_up = st.file_uploader("📂 PCファイルを読込", type=["json"], key="new_pc_ul")
        if new_pc_up and st.button("新規PCとして追加", key="new_pc_btn"):
            dummy_char = create_default_char("新規PC")
            if load_single_char_data(new_pc_up, dummy_char):
                pc_roster.append(dummy_char)
                st.rerun()

        st.markdown("---")
        with st.expander("💾 PC陣営全体の一括保存 / 読込 / AI取り込み", expanded=True):
            st.download_button("📥 PC陣営全体を保存 (DL)", data=lambda: json.dumps(pc_roster, ensure_ascii=False, indent=2), file_name="pc_party.json", mime="application/json", key="save_pc_party_all")
            up_pc = st.file_uploader("📂 PC陣営全体を一括読込", type=["json"], key="load_pc_party_all")
            if up_pc and st.button("PC陣営全体に読込実行", key="load_pc_party_all_btn"):
                if load_roster_data(up_pc, pc_roster): st.rerun()
            st.markdown("---")
            with st.expander("📋 AI提示用プロンプトを表示・コピー"):
                st.caption("以下のプロンプトを魔物データ画像と一緒にAIに送信してください。")
                st.code(AI_PROMPT_TEMPLATE, language="markdown")
            ai_pc = st.text_area("🤖 AI出力データを取り込む (JSON)", height=60, key="ai_in_pc", placeholder="AIが出力したJSONを貼り付け")
            c_pc_ai1, c_pc_ai2 = st.columns(2)
            with c_pc_ai1:
                if st.button("➕ PC陣営に追加", key="ai_btn_pc", use_container_width=True):
                    if import_roster_from_json(ai_pc, pc_roster, replace_all=False): st.rerun()
            with c_pc_ai2:
                if st.button("🔄 PC陣営を全上書き", key="ai_rep_pc", use_container_width=True, help="現在の味方キャラクター（神官PC等の初期キャラ含む）をすべて消去し、このデータで置き換えます"):
                    if import_roster_from_json(ai_pc, pc_roster, replace_all=True): st.rerun()
            st.caption("⚠️ **注意**: AIが出力したデータは誤読・誤判定を含みやすいため、取り込み後にステータスや行動内容の**人間による確認・手直しが必須**です。")

    # 3. エネミーキャラクター
    for j, c in enumerate(en_roster):
        tab_idx = pc_total_tabs + j
        with all_tabs[tab_idx]:
            render_single_char_editor(c, j, en_roster, "en", "エネミー")

    # 4. エネミー追加タブ
    with all_tabs[-1]:
        st.subheader("🔴 エネミーの追加・一括管理")
        col_new_e1, col_new_e2 = st.columns(2)
        with col_new_e1:
            if st.button("➕ 新しいエネミーを追加", key="add_new_en_btn", use_container_width=True):
                en_roster.append(create_default_char("新規エネミー"))
                st.rerun()
        with col_new_e2:
            if st.button("🔄 デフォルト編成に戻す", key="reset_en_default_btn", use_container_width=True, help="敵陣営をデフォルトキャラクター（トロール・ドレイク(竜形態)）に戻します"):
                cleanup_roster_keys(en_roster)
                en_roster.clear()
                en_roster.extend(get_default_enemy_roster())
                st.rerun()

        st.markdown("---")
        st.write("▼ ファイルからエネミーを個別追加")
        new_en_up = st.file_uploader("📂 エネミーファイルを読込", type=["json"], key="new_en_ul")
        if new_en_up and st.button("新規エネミーとして追加", key="new_en_btn"):
            dummy_char = create_default_char("新規エネミー")
            if load_single_char_data(new_en_up, dummy_char):
                en_roster.append(dummy_char)
                st.rerun()

        st.markdown("---")
        with st.expander("💾 エネミー陣営全体の一括保存 / 読込 / AI取り込み", expanded=True):
            st.download_button("📥 敵陣営全体を保存 (DL)", data=lambda: json.dumps(en_roster, ensure_ascii=False, indent=2), file_name="enemy_party.json", mime="application/json", key="save_en_party_all")
            up_en = st.file_uploader("📂 敵陣営全体を一括読込", type=["json"], key="load_en_party_all")
            if up_en and st.button("敵陣営全体に読込実行", key="load_en_party_all_btn"):
                if load_roster_data(up_en, en_roster): st.rerun()
            st.markdown("---")
            with st.expander("📋 AI提示用プロンプトを表示・コピー"):
                st.caption("以下のプロンプトを魔物データ画像と一緒にAIに送信してください。")
                st.code(AI_PROMPT_TEMPLATE, language="markdown")
            ai_en = st.text_area("🤖 AI出力データを取り込む (JSON)", height=60, key="ai_in_en", placeholder="AIが出力したJSONを貼り付け")
            c_en_ai1, c_en_ai2 = st.columns(2)
            with c_en_ai1:
                if st.button("➕ 敵陣営に追加", key="ai_btn_en", use_container_width=True):
                    if import_roster_from_json(ai_en, en_roster, replace_all=False): st.rerun()
            with c_en_ai2:
                if st.button("🔄 敵陣営を全上書き", key="ai_rep_en", use_container_width=True, help="現在の敵キャラクター（マギテック等の初期キャラ含む）をすべて消去し、このデータで置き換えます"):
                    if import_roster_from_json(ai_en, en_roster, replace_all=True): st.rerun()
            st.caption("⚠️ **注意**: AIが出力したデータは誤読・誤判定を含みやすいため、取り込み後にステータスや行動内容の**人間による確認・手直しが必須**です。")

def render_party_status_view(party: Party):
    lines = [f"### {party.side_name} 陣営"]
    if not party.characters:
        lines.append("※キャラクターがいません")
    else:
        for char in party.characters:
            status_icon = "🟢" if not char.is_defeated() else "💀"
            pos_text = f"{char.position}(移動)" if getattr(char, 'initial_position', char.position) != char.position else char.position
            pos_badge = f"<span style='background-color:#555;color:#fff;padding:2px 6px;border-radius:4px;font-size:0.7em;'>{pos_text}</span>" if hasattr(char, 'position') else ""
            lines.append(f"#### {status_icon} {pos_badge} {char.name} <span style='font-size:0.6em;color:gray;'>(方針:{char.target_strategy})</span>")
            for p in char.parts:
                p_icon = "🟢" if p.is_alive else "💀"
                core_label = "🌟 コア" if p.is_core else "🔹 非コア"
                buff_info = ""
                if p.active_buffs:
                    buff_texts = [f"{b.stat}{b.amount:+}({b.duration}T)" for b in p.active_buffs]
                    buff_info = f"<br>&nbsp;&nbsp;&nbsp;&nbsp;🔄 状態: {', '.join(buff_texts)}"
                lines.append(f"&nbsp;&nbsp;{p_icon} **{p.name}** ({core_label}) - HP: {max(0, p.current_hp)}/{p.max_hp} | MP: {max(0, p.current_mp)}/{p.max_mp}{buff_info}")
    st.markdown("\n\n".join(lines), unsafe_allow_html=True)


@st.fragment
def render_single_battle_tab():
    if st.session_state.pc_party is None or st.session_state.enemy_party is None:
        st.info("👈 サイドバーの一番下にある「リセット」ボタンを押して開始してください。")
        return

    pc_party = st.session_state.pc_party
    enemy_party = st.session_state.enemy_party

    col1, col2 = st.columns(2)
    with col1: render_party_status_view(pc_party)
    with col2: render_party_status_view(enemy_party)

    st.markdown("---")

    col_btn1, col_btn2, col_btn3, col_btn4 = st.columns([1, 1, 1.5, 1])
    battle_ongoing = not (pc_party.is_defeated() or enemy_party.is_defeated())

    with col_btn1: step_btn = st.button("1ターン進行", disabled=not battle_ongoing)
    with col_btn2: auto_btn = st.button("決着まで進行", disabled=not battle_ongoing)
    with col_btn3: custom_turns = st.number_input("進行ターン数", min_value=1, max_value=100, value=5, label_visibility="collapsed")
    with col_btn4: custom_step_btn = st.button(f"{custom_turns}ターン進行", disabled=not battle_ongoing)

    def process_turns(num_turns: int):
        for _ in range(num_turns):
            if pc_party.is_defeated() or enemy_party.is_defeated(): break
            st.session_state.battle_log.append(f"--- **第 {st.session_state.turn} ターン** ---")
            logs, is_finished = run_single_turn(pc_party, enemy_party, enable_log=True, turn_num=st.session_state.turn)
            st.session_state.battle_log.extend(logs)
            st.session_state.turn += 1

            if is_finished:
                if pc_party.is_defeated() and enemy_party.is_defeated():
                    st.session_state.battle_log.append(f"🎉 **引き分け（相打ち）！**")
                elif pc_party.is_defeated():
                    st.session_state.battle_log.append(f"🎉 **{enemy_party.side_name}陣営の勝利！**")
                elif enemy_party.is_defeated():
                    st.session_state.battle_log.append(f"🎉 **{pc_party.side_name}陣営の勝利！**")
                break

    if step_btn:
        process_turns(1)
        st.rerun(scope="fragment")

    if custom_step_btn:
        process_turns(custom_turns)
        st.rerun(scope="fragment")

    if auto_btn:
        process_turns(1000)
        st.rerun(scope="fragment")

    c_log_head, c_log_clear = st.columns([4, 1])
    with c_log_head:
        st.subheader("戦闘ログ")
    with c_log_clear:
        st.write("")
        if st.button("🗑️ ログ消去", help="戦闘ログの表示をクリアします"):
            st.session_state.battle_log = ["*(ログがクリアされました)*"]
            st.rerun(scope="fragment")

    log_container = st.container(height=500)
    with log_container:
        if st.session_state.battle_log:
            st.markdown("\n\n".join(st.session_state.battle_log))

@st.fragment
def render_multi_battle_tab():
    if st.session_state.pc_party is None or st.session_state.enemy_party is None:
        st.info("👈 サイドバーの一番下にある「リセット」ボタンを押して開始してください。")
        return

    pc_party = st.session_state.pc_party
    enemy_party = st.session_state.enemy_party

    st.subheader("統計・勝率シミュレーション")
    st.write("現在のステータス・戦略・多部位構成を元に、指定した回数の戦闘を自動実行し、勝率や平均所要ターン数を算出します。")

    sim_col1, sim_col2 = st.columns([1, 2])
    with sim_col1:
        trials = st.number_input("試行回数", min_value=10, max_value=100000, value=100, step=10)
        sim_run_btn = st.button(f"{trials}回 シミュレート実行", type="primary")

    if sim_run_btn:
        with st.spinner("シミュレーション実行中..."):
            st.session_state.sim_results = run_simulation(pc_party, enemy_party, trials)
            st.rerun(scope="fragment")

    if st.session_state.sim_results:
        res = st.session_state.sim_results
        st.markdown("### 📊 シミュレーション結果")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("総試行回数", f"{res['trials']} 回")
        m2.metric(f"PC陣営 勝率", f"{res['win_rate_pc']:.1f} %")
        m3.metric(f"エネミー陣営 勝率", f"{res['win_rate_enemy']:.1f} %")
        m4.metric("平均決着ターン数", f"{res['avg_turns']:.1f} ターン")

        st.markdown(f"""
        - **PC陣営の勝利**: {res['pc_wins']} 回
        - **エネミー陣営の勝利**: {res['enemy_wins']} 回
        - **引き分け(1000ターン超過など)**: {res['draws']} 回
        """)


def render_guide_tab():
    st.subheader("📖 SW2.5 戦闘シミュレーター 総合マニュアル・仕様解説")
    st.write("このシミュレーターの使い方、サイドバーの操作手順、各データの記入方法、および内部で実装されている計算ロジックや戦闘処理の詳細解説です。")

    st.markdown("---")

    # セクション1: クイックスタート
    with st.expander("🚀 クイックスタートガイド (まず動かしてみる)", expanded=True):
        st.markdown("""
        本シミュレーターは **「サイドバーでパーティ・魔物データを設定」** ➔ **「一番下の反映ボタンを押す」** ➔ **「メイン画面でシミュレーション実行」** の3ステップで動作します。

        - **標準デフォルト編成を完備**:
          - 味方陣営（PC）: **腕利きの傭兵**（前衛・物理／全力攻撃）、**人族の神官**（後衛・支援／回復魔法優先・前衛全滅時のみ武器攻撃）
          - 敵陣営（エネミー）: **トロール**（後衛・フォース＆回復魔法・前衛全滅時のみ武器攻撃）、**ドレイク(竜形態)**（前衛・3部位: 牙(胴体)[コア/光のブレス/スパーク], 翼×2[飛翔]）
          - アプリ起動時からすぐに模擬戦を実行でき、いつでもサイドバーの **「🔄 デフォルト編成に戻す」** ボタンで初期状態に復元できます。

        1. **データの準備 (サイドバー)**:
           - 画面左側のサイドバーで、**PC陣営（上段タブ）** と **エネミー陣営（下段タブ）** のキャラクター名、ステータス、行動を設定します。
           - または、後述の「AI取り込み」を使って、ルールブック画像からAIに生成させたJSONを貼り付けるだけで一瞬で登録できます。
        2. **設定の反映**:
           - サイドバー最下部の **「✨ 以上の設定を反映してリセット」** ボタンを押します（画面下に固定表示されています）。
        3. **シミュレーションの実行**:
           - **⚔️ 個別シミュレーション**: 「1ターン進行」や「決着まで進行」を押して、出目や達成値、ダメージ、特殊能力の発動ログを1行ずつ細かく確認できます。
           - **📊 統計シミュレーション**: 試行回数（デフォルト100回、左右の＋−ボタンで10回単位で増減可能）を指定して実行すると、モンテカルロ法により **PC勝率・エネミー勝率・平均決着ターン数** を瞬時に算出し、バランスを客観的に評価できます。
        """)

    # セクション: AI画像読み取りプロンプトと【重要】注意点
    with st.expander("🤖 画像読み取り用AIプロンプト & 【重要】人間による手直しについて", expanded=True):
        st.warning("""
        ### ⚠️ 【重要】AI自動生成データに関する注意点（人間の手直しが必須）
        ChatGPT、Gemini、Claude などのマルチモーダルAIに魔物やキャラクターのデータ画像を渡してJSONを出力させる場合、**現状のAIの画像認識能力では複雑なTRPGの表組や能力テキストの誤読・誤判定（ハルシネーション）が頻繁に発生します**。

        AIによる出力は **「ゼロから手入力する手間を大幅に減らすための下書き・叩き台」** として捉えてください。
        データをシミュレーターに取り込んだ後は、**必ず人間（GMやプレイヤー）の目で元データ画像と照らし合わせ、以下のポイントを手動で修正・確認** してください：

        1. **ステータス数値の確認**: HP、MP、命中力、回避力、打撃点、防護点、生命/精神抵抗、魔力が元のデータと合っているか。
        2. **コア部位のチェック**: コア部位（撃破トリガー）に正しくチェックが入っているか（AIが非コアにしてしまう場合があります）。
        3. **主動作と補助動作の区別**: `▶` (主動作) と `▶▶` (補助動作) が正しく区別されているか。
        4. **特殊能力の達成値・クールダウン**: ブレスなどの大技に `使用間隔: 2`（連続使用不可）や固有の達成値・ダメージが正しく設定されているか。
        5. **部位連動（飛翔など）**: 翼などの前提部位名や、命中ボーナス・回避ボーナスが抜けていないか。
        """)

        st.markdown("#### 📋 AIに画像と一緒に渡すプロンプト (右上のボタンでコピー可能)")
        st.write("以下の枠内のテキストをすべてコピーし、魔物やキャラクターのデータ画像と一緒にAIに送信してください：")
        st.code(AI_PROMPT_TEMPLATE, language="markdown")

    # セクション2: サイドバーの使い方と画面機能
    with st.expander("🎛️ サイドバーの使い方と画面機能", expanded=True):
        st.markdown("""
        #### 1. ↔️ サイドバーの直感的なドラッグ＆ドロップ伸縮
        - サイドバーの右境界線（端）にマウスカーソルを合わせるとリサイズ矢印（`↔`）に変わり、直接ドラッグ＆ドロップすることで横幅（300px〜画面幅いっぱいまで）を自由に変更できます。
        - 変更した横幅はブラウザに自動記憶され、次回起動時も同じ幅で立ち上がります。
        - 右境界線を**ダブルクリック**すると、いつでも標準幅（460px）にワンタッチでリセットできます。

        #### 2. 🏷️ キャラクター切り替えタブ（固定ヘッダー・2段表示・並び順入れ替え対応）
        - サイドバー最上部に、**上段：味方（PC）陣営（青色）**、**下段：敵（エネミー）陣営（赤色）** のタブが固定表示されます。
        - タブとキャラクター名入力欄の間の余白を極限までコンパクトにスリム化しており、無駄な空白なくスマートに編集画面へアクセスできます。
        - キャラクターを切り替えてもヘッダーが画面上部に固定されているため、いつでもワンクリックで別のキャラクターの編集に移れます。
        - キャラクターが増えてもタブの幅が自動調整され、画面外にはみ出さずに収まります。
        - **◀ ▶ 順番移動ボタン**: 各キャラクターの編集エリア上部にある「◀」「▶」ボタンをクリックすることで、同じ陣営内での並び順（左右の位置）を自由に入れ替えることができます。移動後もそのキャラクターの編集タブが自動的にアクティブ状態を維持します。
        - **同名キャラクターの自動連番**: 同じ陣営に「ゴブリン」などの同名キャラクターが複数存在する場合、システムが自動的に「ゴブリンA」「ゴブリンB」とアルファベット連番を付与して識別します。

        #### 3. 👥 キャラクターの追加・複製・削除
        - **➕ 新規キャラを追加**: 陣営に新しいキャラクターを追加します。
        - **👥 キャラ複製**: 該当キャラクターのステータスや全行動設定をそのまま複製します（同種のザコ敵を複数配置したい場合に便利です）。
        - **🗑️ このキャラを削除**: 該当キャラクターを陣営から削除します。

        #### 4. 💾 データの保存・読込・AI取り込み
        - **単体キャラの保存・読込**: 各キャラクターごとに個別にJSON保存・読込が可能です。お気に入りのPCやボスを保存しておけます。
        - **陣営全体の一括保存・読込**: PC陣営全体、または敵陣営全体を1つのJSONファイルとしてまとめて保存・読込できます。
        - **🤖 AI出力データを取り込む (JSON)**: ChatGPT、Gemini、ClaudeなどのAIにルールブックの魔物データ画像と専用プロンプトを渡して出力されたJSONコードを貼り付けて取り込みます。
          - `➕ 陣営に追加`: 現在の陣営にキャラクターを追加します。
          - `🔄 陣営を全上書き`: 初期キャラクター（マギテックや神官PC等）を含め、現在の陣営を全消去してAIデータに一発で置き換えます（手動削除の手間が不要です）。
          - ※キャラクターを削除した場合も内部データが完全にクリアされ、過去のキャラクターが復活して上書きしてしまうことはありません。

        #### 5. ✨ 設定を反映してリセット (固定フッター)
        - サイドバー最下部に固定表示されている **「✨ 以上の設定を反映してリセット」** ボタンを押すことで、編集内容が戦闘エンジンに反映され、初期状態（HP/MP全快、1ターン目開始）にリセットされます。
        """)

    # セクション3: データの記入方法（ステータス・行動・特殊能力）
    with st.expander("📝 データの記入方法（ステータス・行動・特殊能力の詳細）", expanded=True):
        st.markdown("""
        #### 1. キャラクターの基本属性
        - **キャラ名**: キャラクターの名前。
        - **配置（前衛 / 後衛）**:
          - `前衛`: 近接攻撃の優先対象となります。前衛エリア全体攻撃の対象になります。
          - `後衛`: 近接攻撃（前衛優先）から保護されます。前衛が全員倒れると攻撃対象になります。射撃や全エリア攻撃からは狙われます。
        - **方針（ターゲット戦略）**:
          - `倒しやすい部位優先`: 命中率と期待ダメージから、最も素早く撃破できる部位を自動計算して集中攻撃します。
          - `コア部位優先`: コア部位を最優先で狙い、最短でのキャラクター戦闘不能を目指します。
          - `ランダム`: 生存している対象の中からランダムに選択します。
        - **固定値ダイス**:
          - チェックを入れると、2d6を振らずに「出目7（期待値）」固定で行動します。エネミーの固定値戦闘や、乱数ブレのない基準値比較に便利です。
        - **MP切れ時の挙動（後衛配置時）**:
          - `前衛に移動して攻撃に参加する`: 後衛のキャラクターがMP切れ（または残MP不足）で魔法等を行使できなくなった際、自動的に【前衛】へ移動し、近接攻撃（発動条件が『自陣前衛が全滅時のみ』の行動を含む）を解禁して攻撃に参加します。
          - `そのまま後衛にとどまる`: MPが切れても前衛へは出ず、自陣の前衛が全滅するまで後衛に待機します（前衛が生きている間は敵の近接攻撃や前衛エリア攻撃から保護され続けます）。

        #### 2. 📊 部位ステータステーブル（一覧表での直接編集）
        スプレッドシート形式で直接数値を打ち込めます。行の追加や削除も可能です。
        - `コア`: チェックを入れた部位が「コア部位」となります。**すべてのコア部位がHP0になると、そのキャラクターは戦闘不能**になります（多部位ボスの核、PCの本体など）。※コアが1つもない場合は全パーツHP0で敗北。
        - `部位名`: 頭部、胴体、翼、右腕など。同名部位（翼など）がある場合は自動的に「翼A」「翼B」と連番識別されます。
        - `命中力` / `打撃点`: 物理攻撃の判定基準値（2d6+命中力）と固定追加ダメージ（2d6+打撃点）。
        - `回避力` / `防護点`: 物理防御の判定基準値（2d6+回避力）と、物理被ダメージを減算する値。
        - `HP` / `MP`: 部位の最大HPと最大MP。
        - `生命抵抗` / `精神抵抗`: 魔法や毒などの対抗判定基準値（2d6+生命/精神抵抗）。
        - `魔力` / `魔法D`: 魔法行使の判定基準値（2d6+魔力）と魔法ダメージの基準値。

        #### 3. 🎯 部位・行動切り替えタブ（階層型ピルタブ）
        - ステータス一覧表の直下に、多部位キャラクターの場合はまず **第1層：部位切り替えタブ**（青系ピル型: `👑牙(胴体)`, `🔹翼A`, `🔹翼B`, `🔗部位連動・詳細`）が表示されます。
        - 部位を選択すると、その部位が担当する行動のみが **第2層：行動タブ**（赤系ピル型: `🟥基本行動`, `🟩定期行動`, `➕行動追加`）としてすっきりと1列で展開されます。
        - 単一部位キャラクター（一般的なPC等）の場合は、部位タブを挟まずに直接その部位の行動タブ一覧が表示されます。
        - タブをクリックするだけで該当の部位や行動の編集画面を瞬時に切り替え可能で、縦に長いスクロールが不要です。

        #### 4. 🟥 基本行動 (毎ターン行う主動作)
        - 部位が毎ターン標準で行う主動作（通常攻撃やメイン魔法など）です。
        - **補助動作フラグ**: チェックを入れると、主動作を消費せずに毎ターン自動で実行される補助行動（自己強化の練技など）になります。
        - **発動条件**: 「常時」「自陣前衛が全滅時のみ」「自陣前衛が生存中のみ」「自身HP50%以下時のみ」から選択可能（後衛の近接攻撃制限などに便利です）。

        #### 5. 🟩 定期行動 (特殊能力・ブレス・大技など)
        - 「ブレス攻撃」「大魔法」「複数対象攻撃」など、クールダウンを持つ大技を設定できます。
        - **使用間隔 (クールダウン・設定上限18ターン)**:
          - `0`: 毎ターン使用可能。
          - `2`: 1回使ったら次の2ターンは使用不可（3ターン目に再使用可能）。SW2.5の「連続した手番には使えない（1ターンおき）」は `2` に設定します。設定上限は最大 `18` ターンまで対応しています。
        - **開始ターン (何ターン目から実行可能か)**:
          - `1`: 戦闘開始（第1ターン）から使用可能（標準）。
          - `3`: 第3ターン目以降に初めて実行可能になります（ボスが数ターン経過後に繰り出す奥義や、覚醒行動などの再現に便利です）。
        - **主動作の優先順位決定（回復最優先 ＞ 特定対象狙い ＞ 使用間隔優先）**:
          - 回復行動 ＞ 特定対象指定の行動 ＞ 使用間隔（クールダウン）が長い大技の順で優先実行されます。
        - **発動条件 (`exec_cond`)**:
          - `常時 (制限なし)`: 通常の行動。条件を問わず発動します。
          - `自陣前衛が全滅時のみ (後衛の近接など)`: 自陣営の前衛が全滅するまでこの行動を行わず、前衛が崩壊した後に初めて解禁されます（後衛神官やトロールの武器攻撃などに最適）。
          - `自陣前衛が生存中のみ`: 前衛が健在な間のみ実行。
          - `自身HP50%以下時のみ (ピンチ時)`: 自身のHPが半分以下になったピンチ時にのみ発動。
        - **👥 この行動を複製**: ボタンを押すと、設定内容をそのまま複製して下に追加します。派生技や類似特技の作成に便利です。

        #### 5. 🎯 対象・射程・属性の設定
        - **タイプ**:
          - `物理攻撃`: 回避力判定で対抗。命中時に防護点でダメージ軽減。
          - `ガン(回避/防護点無視)`: 相手の回避力・防護点を完全に無視して確定ダメージ。
          - `魔法(生命抵抗/半減)` / `魔法(精神抵抗/半減)`: 抵抗成功時にダメージ半減（切り上げ）。
          - `魔法(魔法抵抗/消滅)` / `魔法(精神抵抗/消滅)`: 抵抗成功時にダメージ0（消滅）。
          - `回復魔法(自身/味方)`: HPが減っている味方を自動選択してHP回復（最大HPを超えない）。
          - `効果付与のみ`: ダメージを与えず、バフ/デバフのみを付与（自動成功または抵抗消滅）。
        - **特定の対象を選択 (特定キャラ・特定部位狙い)**:
          - プルダウンまたは直接入力で、対象陣営に存在する特定のキャラクターや部位（例: `牙(胴体)` や `[部位] ドレイク(竜形態) - 翼A` など）を指定できます。
          - **実行条件**: 指定されたキャラクターまたは部位が対象陣営に**存在し、かつ生存している場合のみ実行**します。存在しない場合や既に撃破されている場合は、この行動はスキップ（実行不可）されます。
          - **実行優先度**: 特定対象が設定された行動は、**回復行動の次に優先（第2優先）**して実行されます（複数ある場合は使用間隔が長い順）。
        - **射程 / 範囲 (`range_pref`)**:
          - `前衛優先`: 前衛に敵がいれば前衛を狙い、全滅したら後衛を狙う（近接攻撃など）。
          - `後衛優先`: 後衛に敵がいれば後衛を狙い、全滅したら前衛を狙う。
          - `すべての敵を攻撃可能`: 前衛・後衛を問わず任意の敵を単体/複数対象で狙える（射撃や単体魔法など）。
          - `前衛エリア全体`: 前衛エリアにいる敵全員を一括攻撃（薙ぎ払い、ドラゴンの尾撃など）。
          - `後衛エリア全体`: 後衛エリアにいる敵全員を一括攻撃。
          - `全エリア全体(戦場のすべての敵)`: 前衛・後衛に関係なく敵全員を一括攻撃（ブレス、大魔法など）。
        - **同時対象数**:
          - エリア全体系を選んだ場合は自動的に「エリア全員 (全体)」となり、指定不要です。
          - それ以外の場合、単体なら `1`、複数なら `2`〜`10`、全体なら `0` を指定します。
        - **消費MP & MP計算**:
          - `1回固定`: 何体巻き込んでも消費MPは一定（ブレスや全体魔法など）。
          - `対象数に比例`: 対象1体ごとにMPを消費（対象拡大魔法など）。
        - **独自の達成値・ダメージを使う**:
          - チェックを入れると、部位自身のステータスではなく、その特技固有の達成値基準値（例: ブレスの行使値24）やダメージ（例: 2d+25の25）を適用できます。

        #### 6. 🟪 追加効果 (バフ / デバフ / 毒 / スリップ・2種類まで設定可能)
        - 攻撃命中時や効果行使時に、自身または攻撃対象に付与するステータス変化を設定できます。
        - **最大2種類（またはそれ以上）の追加効果を同時に設定可能**（「【追加効果 1】」「【追加効果 2】」として表示され、「➕ 2つ目の効果を追加」ボタンで追加可能）。
        - 対象ステータス: 命中力、回避力、打撃点、防護点、生命抵抗力、精神抵抗力、毎ターンHP減少(毒)、毎ターンMP減少。
        - 変化量（+または-）と持続ターン数（0で永続）を指定可能。全力攻撃の自身回避低下（-2）や毒の持続ダメージなども自由自在に再現できます。

        #### 7. 🔗 特殊能力・部位連動 (飛翔 / 庇う / 長い首など)
        SW2.5のモンスターに多く見られる「特定の部位が生きていないと使えない能力やボーナス」を完全再現します。
        - **前提部位名**: プルダウン（選択ボックス）から、そのキャラクターの他の部位（連番付き: 「翼A」「翼B」「胴体」など、同名部位が複数ある場合は「翼 (いずれかの翼が生存)」も選択可）を直感的に選択できます。手動での打ち間違いがなくなり、安全かつスムーズに設定可能です。
        - **命中ボーナス (`dep_acc`)**: 前提部位が生存している間、自身の命中力判定に加算（例: 飛翔で +1）。
        - **回避ボーナス (`dep_eva`)**: 前提部位が生存している間、自身の回避力判定に加算（例: 飛翔で +1）。
        - **前提部位の生存中は攻撃対象不可 (`dep_protect`)**: 前提部位が生存している間、敵の攻撃対象に選ばれなくなります（庇う、胴体内部のコア、長い首による保護など）。

        #### 8. ターン開始時HP/MP増減 (再生 / 自傷)
        - ターン開始時に自動で発生するHP/MPの増減です。
        - **マイナスの値を入力するとHPが回復**します（例: 再生能力5なら `-5` と入力）。
        """)

    # セクション4: 内部処理・計算エンジン仕様
    with st.expander("⚙️ 内部処理・計算エンジン仕様（判定式・アルゴリズム）", expanded=True):
        st.markdown("""
        #### 1. ダイス計算式と出目ルール
        - **通常判定**: 2d6 (2〜12) のダイスロールを行います。
        - **2d6の確率分布**: 独立した2個の1d6（1〜6）を別々に振った合計値を計算しています。現実のダイスを2個振った時と数学的に100%同一の確率分布（全36通り・期待値7が16.67%、2と12が各2.78%の三角分布）を厳密に再現しています。
        - **ピンゾロ (出目2)**: **自動失敗**。攻撃側なら命中せず終了、防御側なら抵抗に失敗します。
        - **クリティカル (出目12)**: **自動成功**。相手の回避や抵抗の達成値に関わらず必中します。
        - **固定値モード**: 出目を常に「7」として計算します（達成値 = 基準値 + 7、ダメージ = 固定値 + 7）。

        #### 2. 対抗判定とダメージ計算
        - **物理攻撃**:
          - 命中達成値: $2\\mathrm{d}6 + \\text{命中力}$ （連動ボーナス・バフ加算）
          - 回避達成値: $2\\mathrm{d}6 + \\text{回避力}$ （連動ボーナス・バフ加算）
          - 判定: $\\text{命中達成値} \\ge \\text{回避達成値}$ で命中。
          - ダメージ: $\\max(0, 2\\mathrm{d}6 + \\text{打撃点} - \\text{防護点})$
        - **ガン攻撃**:
          - 回避判定なし・防護点減算なしで確定ダメージ。
        - **魔法・特殊能力**:
          - 行使達成値: $2\\mathrm{d}6 + \\text{魔力(または固有達成値)}$
          - 抵抗達成値: $2\\mathrm{d}6 + \\text{生命抵抗力(または精神抵抗力)}$
          - 判定: $\\text{行使達成値} > \\text{抵抗達成値}$ で抵抗失敗（同値は抵抗成功）。
          - 「半減」時: 抵抗成功ならダメージを $\\lceil \\text{ダメージ} / 2 \\rceil$ （端数切り上げ）。
          - 「消滅」時: 抵抗成功ならダメージ $0$。

        #### 3. バフ・デバフの持続ターン経過タイミング（SW2.5ルール完全準拠）
        - ターンをまたいで持続するバフやデバフ（全力攻撃の自身回避低下、魔力撃の精神抵抗低下、毒、支援魔法など）の持続時間は、**「その効果を付与したキャラクターが次のターンで行動する直前」に1ターン経過（減算）**されます。
        - これにより、「全力攻撃を使ったキャラクターへの敵の反撃が確実にデバフ込みで判定される」「魔力撃で精神抵抗が下がったボスに対して、次ターンの手番が回る前にPC神官や魔法使いが魔法を撃ち込む」といったSW2.5本来の戦術的駆け引きが100%正確に再現されます。

        #### 4. 主動作の決定ロジック（行動の優先順位）
        部位が手番を迎えた際、以下の優先順位に従って実行する主動作を自動決定します：
        1. **補助動作の全実行**: `is_minor: true` の行動は主動作を消費しないため、条件を満たしていれば手番開始時にすべて自動実行されます。
        2. **回復行動の最優先**: 味方に設定HP%以下の負傷者が存在し、回復行動が使用可能な場合、他のいかなる攻撃行動よりも最優先で実行します（複数ある場合は使用間隔が長い大技を優先）。
        3. **特定対象が指定された行動の優先（第2優先）**: `特定の対象 (特定部位・特定キャラ狙い)` が設定されている行動は、指定された対象が生きていれば回復行動の次に優先して実行されます（複数ある場合は使用間隔が長い順）。指定対象が存在しないか撃破済みの場合は実行されません。
        4. **定期行動の使用間隔（クールダウン）優先**: 通常の定期行動（特定対象なし）が複数ある場合、**使用間隔（クールダウン: 0〜18）がより長い大技（ブレスや強力な魔法など）を優先して実行**します（同値ならリストの先頭を優先）。
        5. **基本行動へのフォールバック**: 実行可能な定期行動がない（すべてCT中またはMP不足、対象不在など）場合、基本行動（通常攻撃）を実行します（基本行動にも特定対象がある場合はその生存時のみ）。

        #### 5. MP管理と自動フォールバック・後衛のMP切れ時挙動
        - 行動開始時に必要MPが不足している場合、大技は不発となりますが、**手番を無駄にせず自動的に基本行動（通常攻撃）を実行**します。
        - さらに、後衛に配置されたキャラクター（トロールや神官など）がMP切れ・MP不足で後衛用の魔法等を使えなくなった場合、キャラ設定の **「MP切れ時の挙動 (後衛配置時)」** に応じて、**自動的に前衛へ移動して近接攻撃（『自陣前衛が全滅時のみ』の武器攻撃や全力攻撃など）に参加する**か、**そのまま後衛にとどまり前衛が倒れるまで待機する**かを切り替えられます。

        #### 6. 高度なターゲット選定スコアリング（AI思考ルーチン）
        「倒しやすい部位優先」が選択された場合、攻撃部位は以下の評価スコアを算出して標的を決定します:
        $$\\text{Score} = \\frac{\\text{命中確率} \\times \\text{期待実効DPS}}{\\text{対象部位の現在HP}} + \\text{乱数}$$
        - 相手の回避力や防護点、抵抗力を加味して「手番あたりの戦力削減効率」を最大化する部位を自動的に集中攻撃します。
        - コア部位優先の場合も、複数コアがあるときはより倒しやすいコアを優先します。

        #### 7. 敗北・決着条件
        - **キャラクターの敗北**:
          - コア部位を持つキャラ: **すべてのコア部位のHPが0以下**になった時点で戦闘不能。
          - コア部位を持たないキャラ: **すべての部位のHPが0以下**になった時点で戦闘不能。
        - **陣営の敗北**: パーティ内の全キャラクターが戦闘不能になった時点で決着。
        - **引き分け**: 1000ターン経過しても両者決着がつかない場合は引き分けとして処理。
        """)

    # セクション5: モンスター再現のレシピ集
    with st.expander("💡 実践！SW2.5魔物データ再現テクニック", expanded=False):
        st.markdown("""
        | 再現したい能力 | 設定方法 |
        | :--- | :--- |
        | **◯飛翔 / ◯飛翔II** | 連動させたい部位の「前提部位名」プルダウンで `翼 (いずれかの翼が生存)`（または `翼A` / `翼B`）を選択し、`命中ボーナス: 1`, `回避ボーナス: 1` を設定。 |
        | **◯庇う / ◯保護されたコア** | コア部位の「前提部位名」プルダウンで `胴体` などを選択し、`前提部位の生存中は攻撃対象不可` にチェック。 |
        | **◯再生 = X点** | 部位の `ターン開始時HP増減` に `-X` を設定（例: 再生5点なら `-5`）。 |
        | **▶ブレス (連続使用不可)** | 定期行動を作成し、`使用間隔: 2`、`射程/範囲: 全エリア全体(戦場のすべての敵)`、`タイプ: 魔法(生命抵抗/半減)` に設定。 |
        | **▶テイルスイング / 薙ぎ払い** | 定期行動を作成し、`射程/範囲: 前衛エリア全体`、`タイプ: 物理攻撃`、`対象数: 0(エリア全員)` に設定。 |
        | **▶▶練技 (ビートルスキン等)** | 定期行動で `補助動作: チェック`、`対象: 自身`、追加効果に `防護点 +2 (3T)` を設定。 |
        | **毒 / 腐食液** | 行動の追加効果で `ステータス: 毎ターンHP減少(毒)`、`変化量: ダメージ数` を設定。 |
        """)

    # セクション6: 動作環境・誰でも使える起動方法
    with st.expander("💻 動作環境・誰でも使える起動方法について", expanded=True):
        st.markdown("""
        ### Q. 『app.py』があるパソコンなら、コマンドプロンプトにコマンドを入力するだけで誰でも使用可能ですか？

        **A. はい、完全にその通りです！**

        本シミュレーターは Python の標準的なオープンソースWebフレームワークである **Streamlit** で構築されています。
        外部の有料サーバーやWebホスティングは一切不要で、Pythonが入っているパソコンであれば **コマンドプロンプトに簡単なコマンドを打ち込むだけで、誰でも無料でローカルWebアプリとして利用可能** です。

        ---

        #### 🔰 初めて起動する場合の手順（たったこれだけ）

        1. **事前準備（最初の一度だけ）**:
           - [Python公式サイト (python.org)](https://www.python.org/) からPython（3.8以上）をインストールします。
             *(※Windowsの場合、インストール画面の最初で **「Add Python to PATH」** に必ずチェックを入れてください)*
           - コマンドプロンプトを開き、以下のコマンドを1行入力してEnterを押します：
           ```cmd
           pip install streamlit
           ```

        2. **シミュレーターの起動**:
           - `app.py` があるフォルダでコマンドプロンプト（またはPowerShell）を開きます。
             *(フォルダのアドレスバーに `cmd` と入力してEnterを押すと、その場所でコマンドプロンプトが一瞬で開きます)*
           - 以下のコマンドを実行します：
           ```cmd
           streamlit run app.py
           ```
           *(※環境によっては `python -m streamlit run app.py` でも起動できます)*

        3. **ブラウザで画面が開きます**:
           - コマンドを実行すると自動的にブラウザ（Chrome, Edge等）が立ち上がり、`http://localhost:8501` でシミュレーターの全機能が利用可能になります！

        ---

        #### 💡 選べる起動方法（お好みに合わせてご利用ください）
        1. **【最もおすすめ！】専用実行ファイル `SW25_Simulator.exe` をダブルクリック**:
           - 完全なWindows実行ファイル（.exe）です。
           - 黒いコマンドプロンプト画面を一切出さず、ブラウザのアドレスバーや余計なタブがない「専用の独立アプリウィンドウ」として立ち上がります！
        2. **`アプリ起動(独立ウィンドウ).vbs` をダブルクリック**:
           - VBScript経由でバックグラウンド実行します。同様に黒窓を出さず、独立アプリウィンドウとして立ち上がります。
        3. **通常のバッチファイル `起動.bat` をダブルクリック**:
           - 黒い画面（ログ確認用）が立ち上がり、既定のWebブラウザのタブとして開きます。

        ※アプリウィンドウ（.exe または .vbs）で起動したアプリを終了したい場合は、フォルダ内の **`アプリ終了.bat`** をダブルクリックするとワンクリックでサーバーごと安全に完全終了できます。

        ---

        #### 🏛️ システム構成・動作原理・使用技術（正式完成版仕様）
        - **バージョン・リリース日**: 正式完成版 Version 1.0.0 (2026年10月12日版)
        - **開発言語・動作環境**: Python 3.8+ (推奨: Python 3.11)
        - **UI / Webフレームワーク**: Streamlit (リアクティブなステート管理 `st.session_state` による高速画面描画)
        - **独立アプリ化技術**:
          - **ネイティブ実行ファイル**: PyInstaller による `SW25_Simulator.exe`（Windows APIレベルでの黒窓非表示・バックグラウンドプロセス管理）
          - **独立ウィンドウ表示**: Google Chrome / Microsoft Edge の `--app=http://localhost:8501` 引数（ブラウザのアドレスバー・余計なタブを一切排除したネイティブアプリUI化）
          - **スクリプト起動**: Windows Script Host (`wscript.exe`) による `アプリ起動(独立ウィンドウ).vbs`、および `run_app.bat`
          - **プロセス制御**: `アプリ終了.bat` によるポート8501リスニングプロセスのワンクリック安全終了
        - **シミュレーション・計算エンジン**:
          - **ダイス計算**: Python `random.randint(1, 6) + random.randint(1, 6)` による正確な2d6確率分布（合計2〜12、出目7が最頻値 16.67%）
          - **威力表レーティング**: SW2.5公式威力表（Key 10〜100、出目3〜12）の全数値を完全実装
          - **会心・クリティカル判定**: 出目10以上での連続威力表ロール・ダメージ加算処理
          - **持続ターン減算ロジック**: バフ・デバフの持続ターンは、付与したキャラクターの手番直前に1ターン経過をカウント（付与ラウンド中および次巡の手番直前までの効果持続を保証）
          - **思考ルーチンAI優先度**:
            1. 味方に負傷者が存在する場合の回復魔法・回復行動（最優先）
            2. 特定部位が指定された行動（生存中なら最優先、クールダウン長優先）
            3. 通常の定期行動（使用可能間隔が長い大技を最優先）
            4. 基本攻撃（通常武器攻撃など）

        ---

        #### 🔒 プライバシー・オフライン性について
        - **完全ローカル動作**: 入力したキャラクターデータや戦闘ログが外部サーバーへ送信されることは一切ありません。
        - **完全オフライン動作**: 初回の `pip install streamlit` 以降は、インターネットに接続されていないオフライン環境でもすべての機能が軽快に動作します。
        """)


def main():
    st.set_page_config(page_title="SW2.5 戦闘シミュレーター 正式完成版 (2026-10-12)", layout="wide")
    st.markdown('<meta name="google" content="notranslate">', unsafe_allow_html=True)

    st.markdown("""
    <style>
    :root {
        --sw25-sidebar-w: 460px;
    }
    section[data-testid="stSidebar"] {
        width: var(--sw25-sidebar-w, 460px) !important;
        min-width: var(--sw25-sidebar-w, 460px) !important;
        position: relative !important;
    }
    /* --- 最上部: キャラクター切り替えタブ (上段: 味方 / 下段: 敵) --- */
    section[data-testid="stSidebar"] [role="tablist"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *),
    section[data-testid="stSidebar"] div[data-baseweb="tab-list"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *),
    section[data-testid="stSidebar"] .sw25-char-tablist {
        position: fixed !important;
        top: 0px !important;
        left: 0px !important;
        width: var(--sw25-sidebar-w, 460px) !important;
        height: 68px !important;
        min-height: 68px !important;
        max-height: 68px !important;
        z-index: 10000 !important;
        background-color: var(--secondary-background-color, #f0f2f6) !important;
        padding: 1px 8px 1px 8px !important;
        box-shadow: 0 3px 6px -2px rgba(0,0,0,0.12) !important;
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: wrap !important;
        align-items: center !important;
        box-sizing: border-box !important;
        gap: 0px !important;
        row-gap: 2px !important;
        column-gap: 0px !important;
        overflow: visible !important;
        overflow-x: visible !important;
        overflow-y: visible !important;
    }
    section[data-testid="stSidebar"] button[data-testid="stTabsScrollLeft"],
    section[data-testid="stSidebar"] button[data-testid="stTabsScrollRight"],
    section[data-testid="stSidebar"] [data-testid="stTabsScrollLeft"],
    section[data-testid="stSidebar"] [data-testid="stTabsScrollRight"],
    section[data-testid="stSidebar"] .react-aria-SelectionIndicator {
        display: none !important;
    }
    section[data-testid="stSidebar"] [role="tab"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *),
    section[data-testid="stSidebar"] [data-testid="stTab"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *),
    section[data-testid="stSidebar"] .sw25-char-tablist [role="tab"],
    section[data-testid="stSidebar"] .sw25-char-tablist [data-testid="stTab"] {
        height: 32px !important;
        min-height: 32px !important;
        max-height: 32px !important;
        padding: 0px 4px !important;
        margin: 0px !important;
        font-size: 0.78rem !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        box-sizing: border-box !important;
        cursor: pointer !important;
        border-radius: 4px 4px 0 0 !important;
    }
    section[data-testid="stSidebar"] [role="tab"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *)[aria-selected="true"],
    section[data-testid="stSidebar"] [role="tab"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *)[data-selected="true"],
    section[data-testid="stSidebar"] [data-testid="stTab"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *)[aria-selected="true"],
    section[data-testid="stSidebar"] [data-testid="stTab"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *)[data-selected="true"],
    section[data-testid="stSidebar"] .sw25-char-tablist [role="tab"][aria-selected="true"],
    section[data-testid="stSidebar"] .sw25-char-tablist [data-testid="stTab"][aria-selected="true"] {
        background-color: rgba(255, 255, 255, 0.6) !important;
        box-shadow: inset 0 0 0 1px rgba(0,0,0,0.12) !important;
        font-weight: 800 !important;
    }
    section[data-testid="stSidebar"] [role="tab"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *) div,
    section[data-testid="stSidebar"] [role="tab"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *) p,
    section[data-testid="stSidebar"] [role="tab"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *) span,
    section[data-testid="stSidebar"] [data-testid="stTab"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *) div,
    section[data-testid="stSidebar"] [data-testid="stTab"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *) p,
    section[data-testid="stSidebar"] [data-testid="stTab"]:not([role="tabpanel"] *):not([data-testid="stTabPanel"] *) span,
    section[data-testid="stSidebar"] .sw25-char-tablist [role="tab"] div,
    section[data-testid="stSidebar"] .sw25-char-tablist [role="tab"] p,
    section[data-testid="stSidebar"] .sw25-char-tablist [role="tab"] span {
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        white-space: nowrap !important;
        width: 100% !important;
        text-align: center !important;
        display: block !important;
        font-size: 0.76rem !important;
        line-height: 1.2 !important;
    }

    /* --- キャラクタータブ直下の余白を詰めてスマートにする --- */
    section[data-testid="stSidebar"] div[data-testid="stTabs"] {
        margin-top: 0px !important;
        padding-top: 0px !important;
    }
    section[data-testid="stSidebar"] [data-testid="stTabPanel"],
    section[data-testid="stSidebar"] [role="tabpanel"] {
        padding-top: 0px !important;
        margin-top: 0px !important;
        padding-left: 0px !important;
        padding-right: 0px !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] > div.stVerticalBlock {
        gap: 0.25rem !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stElementContainer"]:empty,
    section[data-testid="stSidebar"] div[data-testid="stElementContainer"]:has(style:only-child) {
        display: none !important;
        height: 0px !important;
        margin: 0px !important;
        padding: 0px !important;
    }

    /* --- 部位切り替えタブ (多部位モンスター用: 青系ピル型タブ) --- */
    section[data-testid="stSidebar"] .sw25-part-tablist {
        position: static !important;
        top: auto !important;
        left: auto !important;
        width: 100% !important;
        height: auto !important;
        min-height: 0px !important;
        max-height: none !important;
        z-index: 10 !important;
        box-shadow: none !important;
        background-color: rgba(25, 118, 210, 0.05) !important;
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: wrap !important;
        gap: 3px !important;
        padding: 3px !important;
        margin-top: 6px !important;
        margin-bottom: 6px !important;
        border-radius: 6px !important;
        border: 1px solid rgba(25, 118, 210, 0.2) !important;
        box-sizing: border-box !important;
        overflow: visible !important;
    }
    section[data-testid="stSidebar"] .sw25-part-tablist [role="tab"],
    section[data-testid="stSidebar"] .sw25-part-tablist [data-testid="stTab"] {
        flex: 1 1 auto !important;
        width: auto !important;
        max-width: none !important;
        min-width: 55px !important;
        height: 29px !important;
        min-height: 29px !important;
        max-height: 29px !important;
        padding: 2px 8px !important;
        margin: 1px !important;
        font-size: 0.77rem !important;
        font-weight: 600 !important;
        border-radius: 4px !important;
        border: 1px solid rgba(25, 118, 210, 0.25) !important;
        background-color: var(--secondary-background-color, #ffffff) !important;
        color: #1976d2 !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        box-sizing: border-box !important;
        cursor: pointer !important;
        transition: all 0.15s ease !important;
    }
    section[data-testid="stSidebar"] .sw25-part-tablist [role="tab"]:hover,
    section[data-testid="stSidebar"] .sw25-part-tablist [data-testid="stTab"]:hover {
        background-color: rgba(25, 118, 210, 0.12) !important;
        border-color: #1976d2 !important;
    }
    section[data-testid="stSidebar"] .sw25-part-tablist [role="tab"][aria-selected="true"],
    section[data-testid="stSidebar"] .sw25-part-tablist [data-testid="stTab"][aria-selected="true"] {
        background-color: #1976d2 !important;
        color: #ffffff !important;
        font-weight: bold !important;
        border-color: #1976d2 !important;
        box-shadow: 0 1px 4px rgba(25, 118, 210, 0.35) !important;
    }
    section[data-testid="stSidebar"] .sw25-part-tablist [role="tab"][aria-selected="true"] * {
        color: #ffffff !important;
    }
    section[data-testid="stSidebar"] .sw25-part-tablist [data-baseweb="tab-highlight"] {
        display: none !important;
    }

    /* --- 行動切り替えタブ (各部位の行動: 赤系ピル型タブ) --- */
    section[data-testid="stSidebar"] [role="tabpanel"] [role="tablist"],
    section[data-testid="stSidebar"] [role="tabpanel"] div[data-baseweb="tab-list"],
    section[data-testid="stSidebar"] [data-testid="stTabPanel"] [role="tablist"],
    section[data-testid="stSidebar"] [data-testid="stTabPanel"] div[data-baseweb="tab-list"],
    section[data-testid="stSidebar"] .sw25-action-tablist {
        position: static !important;
        top: auto !important;
        left: auto !important;
        right: auto !important;
        bottom: auto !important;
        width: 100% !important;
        height: auto !important;
        min-height: 0px !important;
        max-height: none !important;
        z-index: 10 !important;
        box-shadow: none !important;
        background-color: rgba(0, 0, 0, 0.03) !important;
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: wrap !important;
        gap: 3px !important;
        padding: 3px !important;
        margin-top: 4px !important;
        margin-bottom: 6px !important;
        border-radius: 6px !important;
        border: 1px solid rgba(128, 128, 128, 0.2) !important;
        box-sizing: border-box !important;
        overflow: visible !important;
    }
    section[data-testid="stSidebar"] [role="tabpanel"] [role="tablist"] [role="tab"],
    section[data-testid="stSidebar"] [data-testid="stTabPanel"] [role="tablist"] [role="tab"],
    section[data-testid="stSidebar"] [role="tabpanel"] [data-testid="stTab"],
    section[data-testid="stSidebar"] [data-testid="stTabPanel"] [data-testid="stTab"],
    section[data-testid="stSidebar"] .sw25-action-tablist [role="tab"],
    section[data-testid="stSidebar"] .sw25-action-tablist [data-testid="stTab"] {
        flex: 1 1 auto !important;
        width: auto !important;
        max-width: none !important;
        min-width: 50px !important;
        height: 27px !important;
        min-height: 27px !important;
        max-height: 27px !important;
        padding: 1px 6px !important;
        margin: 1px !important;
        font-size: 0.74rem !important;
        font-weight: 600 !important;
        border-radius: 4px !important;
        border: 1px solid rgba(128, 128, 128, 0.22) !important;
        border-bottom: 1px solid rgba(128, 128, 128, 0.22) !important;
        background-color: var(--secondary-background-color, #ffffff) !important;
        color: inherit !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        box-sizing: border-box !important;
        cursor: pointer !important;
        transition: all 0.15s ease !important;
    }
    section[data-testid="stSidebar"] [role="tabpanel"] [role="tablist"] [role="tab"]:hover,
    section[data-testid="stSidebar"] [data-testid="stTabPanel"] [role="tablist"] [role="tab"]:hover,
    section[data-testid="stSidebar"] [role="tabpanel"] [data-testid="stTab"]:hover,
    section[data-testid="stSidebar"] [data-testid="stTabPanel"] [data-testid="stTab"]:hover,
    section[data-testid="stSidebar"] .sw25-action-tablist [role="tab"]:hover {
        background-color: rgba(255, 75, 75, 0.12) !important;
        border-color: rgba(255, 75, 75, 0.5) !important;
        border-bottom-color: rgba(255, 75, 75, 0.5) !important;
    }
    section[data-testid="stSidebar"] [role="tabpanel"] [role="tablist"] [role="tab"][aria-selected="true"],
    section[data-testid="stSidebar"] [data-testid="stTabPanel"] [role="tablist"] [role="tab"][aria-selected="true"],
    section[data-testid="stSidebar"] [role="tabpanel"] [data-testid="stTab"][aria-selected="true"],
    section[data-testid="stSidebar"] [data-testid="stTabPanel"] [data-testid="stTab"][aria-selected="true"],
    section[data-testid="stSidebar"] .sw25-action-tablist [role="tab"][aria-selected="true"] {
        background-color: #ff4b4b !important;
        color: #ffffff !important;
        font-weight: bold !important;
        border-color: #ff4b4b !important;
        border-bottom: 1px solid #ff4b4b !important;
        box-shadow: 0 1px 3px rgba(255, 75, 75, 0.4) !important;
    }
    section[data-testid="stSidebar"] [role="tabpanel"] [role="tablist"] [role="tab"] div,
    section[data-testid="stSidebar"] [role="tabpanel"] [role="tablist"] [role="tab"] p,
    section[data-testid="stSidebar"] [role="tabpanel"] [role="tablist"] [role="tab"] span,
    section[data-testid="stSidebar"] [data-testid="stTabPanel"] [role="tab"] div,
    section[data-testid="stSidebar"] [data-testid="stTabPanel"] [role="tab"] p,
    section[data-testid="stSidebar"] [data-testid="stTabPanel"] [role="tab"] span,
    section[data-testid="stSidebar"] .sw25-action-tablist [role="tab"] div,
    section[data-testid="stSidebar"] .sw25-action-tablist [role="tab"] p,
    section[data-testid="stSidebar"] .sw25-action-tablist [role="tab"] span {
        font-size: 0.74rem !important;
        font-weight: inherit !important;
        color: inherit !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
    }
    section[data-testid="stSidebar"] [role="tabpanel"] [role="tablist"] [role="tab"][aria-selected="true"] *,
    section[data-testid="stSidebar"] [data-testid="stTabPanel"] [role="tablist"] [role="tab"][aria-selected="true"] *,
    section[data-testid="stSidebar"] .sw25-action-tablist [role="tab"][aria-selected="true"] * {
        color: #ffffff !important;
    }
    section[data-testid="stSidebar"] [role="tabpanel"] [data-baseweb="tab-highlight"],
    section[data-testid="stSidebar"] [data-testid="stTabPanel"] [data-baseweb="tab-highlight"],
    section[data-testid="stSidebar"] .sw25-action-tablist [data-baseweb="tab-highlight"] {
        display: none !important;
    }
    /* --- サイドバー固定フッター (反映してリセットボタン) --- */
    section[data-testid="stSidebar"] div.stButton:has(button[kind="primary"]),
    section[data-testid="stSidebar"] div[data-testid="stElementContainer"]:has(button[kind="primary"]) {
        position: fixed !important;
        bottom: 0px !important;
        left: 0px !important;
        width: var(--sw25-sidebar-w, 460px) !important;
        z-index: 10002 !important;
        background-color: var(--secondary-background-color, #f0f2f6) !important;
        padding: 8px 16px 12px 16px !important;
        border-top: 1px solid rgba(128, 128, 128, 0.25) !important;
        box-shadow: 0 -4px 6px -2px rgba(0,0,0,0.1) !important;
        box-sizing: border-box !important;
    }
    /* スクロールコンテンツの上部・下部余白 (タブ68px + 下部ボタン約65px) */
    section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] {
        padding-top: 68px !important;
        padding-bottom: 72px !important;
    }
    /* --- サイドバー右端のドラッグリサイズ用ハンドル --- */
    #sw25-sidebar-resizer {
        position: absolute !important;
        top: 0px !important;
        right: 0px !important;
        width: 10px !important;
        height: 100% !important;
        cursor: col-resize !important;
        z-index: 999999 !important;
        user-select: none !important;
        -webkit-user-select: none !important;
        background-color: transparent !important;
        transition: background-color 0.15s ease, border-right 0.15s ease !important;
    }
    #sw25-sidebar-resizer:hover,
    #sw25-sidebar-resizer.dragging {
        background-color: rgba(255, 75, 75, 0.25) !important;
        border-right: 3px solid #ff4b4b !important;
    }
    /* 余白をタイトにして情報密度を上げる */
    section[data-testid="stSidebar"] div.stVerticalBlock {
        gap: 0.45rem !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stElementContainer"] {
        margin-bottom: 0px !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stExpander"] {
        margin-bottom: 0.3rem !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stExpander"] details summary {
        padding-top: 0.25rem !important;
        padding-bottom: 0.25rem !important;
    }
    section[data-testid="stSidebar"] .stNumberInput,
    section[data-testid="stSidebar"] .stTextInput,
    section[data-testid="stSidebar"] .stSelectbox {
        margin-bottom: 0px !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        padding: 0.45rem 0.6rem !important;
        margin-bottom: 0.35rem !important;
    }
    hr {
        margin-top: 0.5rem !important;
        margin-bottom: 0.5rem !important;
    }
    </style>
    """, unsafe_allow_html=True)

    st.title("⚔️ ソード・ワールド2.5 戦闘シミュレーター 正式完成版")
    st.caption("Release Version 1.0.0 (2026年10月12日版) | パーティ制・多部位・独自思考AI・完全オフライン対応")

    if 'battle_log' not in st.session_state: st.session_state.battle_log = []
    if 'pc_party' not in st.session_state: st.session_state.pc_party = None
    if 'enemy_party' not in st.session_state: st.session_state.enemy_party = None
    if 'turn' not in st.session_state: st.session_state.turn = 1
    if 'sim_results' not in st.session_state: st.session_state.sim_results = None

    # デフォルトキャラクターの初期化 & マイグレーション
    roster_init_ver = st.session_state.get('roster_init_version', 0)
    if 'draft_pc_roster' not in st.session_state or roster_init_ver < 7:
        cleanup_roster_keys(st.session_state.get('draft_pc_roster', []))
        st.session_state.draft_pc_roster = get_default_pc_roster()
        st.session_state.pc_party = None

    if 'draft_en_roster' not in st.session_state or roster_init_ver < 7:
        cleanup_roster_keys(st.session_state.get('draft_en_roster', []))
        st.session_state.draft_en_roster = get_default_enemy_roster()
        st.session_state.enemy_party = None

    st.session_state['roster_init_version'] = 7

    for c in st.session_state.draft_pc_roster:
        if "char_id" not in c or not c["char_id"]: c["char_id"] = str(uuid.uuid4())
        for p in c.get("parts", []):
            if "id" not in p or not p["id"]: p["id"] = str(uuid.uuid4())
    for c in st.session_state.draft_en_roster:
        if "char_id" not in c or not c["char_id"]: c["char_id"] = str(uuid.uuid4())
        for p in c.get("parts", []):
            if "id" not in p or not p["id"]: p["id"] = str(uuid.uuid4())

    if st.session_state.pc_party is None:
        st.session_state.pc_party = convert_roster_to_party(st.session_state.draft_pc_roster, "PC")
    if st.session_state.enemy_party is None:
        st.session_state.enemy_party = convert_roster_to_party(st.session_state.draft_en_roster, "エネミー")


    # --- サイドバー直接ドラッグリサイズ＆タブスコープ制御スクリプト注入 (MutationObserver方式・待機時CPU負荷ゼロ) ---
    sidebar_resizer_js = """
    <script>
    (function() {
        let rafScheduled = false;

        function scheduleRunAll() {
            if (!rafScheduled) {
                rafScheduled = true;
                (window.parent.requestAnimationFrame || requestAnimationFrame)(runAll);
            }
        }

        function initSidebarResizer(doc, sidebar) {
            // 保存された幅の復元 (初回のみ)
            if (!sidebar._sw25WidthRestored) {
                sidebar._sw25WidthRestored = true;
                const savedWidth = localStorage.getItem('sw25_sidebar_width');
                if (savedWidth) {
                    const w = parseInt(savedWidth);
                    if (w >= 300 && w <= window.parent.innerWidth - 100) {
                        doc.documentElement.style.setProperty('--sw25-sidebar-w', w + 'px');
                        sidebar.style.setProperty('width', w + 'px', 'important');
                        sidebar.style.setProperty('min-width', w + 'px', 'important');
                    }
                }
            }

            // リサイズハンドルの作成・追加
            let resizer = doc.getElementById('sw25-sidebar-resizer');
            if (!resizer) {
                resizer = doc.createElement('div');
                resizer.id = 'sw25-sidebar-resizer';
                resizer.setAttribute('title', 'ドラッグして幅を自由に変更 / ダブルクリックで初期幅(460px)にリセット');
                sidebar.appendChild(resizer);
            }

            if (!resizer._boundDrag) {
                resizer._boundDrag = true;
                let isDragging = false;
                let startX = 0;
                let startW = 0;

                function onPointerMove(e) {
                    if (!isDragging) return;
                    const deltaX = e.clientX - startX;
                    let newWidth = Math.round(startW + deltaX);
                    if (newWidth < 300) newWidth = 300;
                    const maxW = Math.max(600, (window.parent.innerWidth || 1200) - 150);
                    if (newWidth > maxW) newWidth = maxW;

                    doc.documentElement.style.setProperty('--sw25-sidebar-w', newWidth + 'px');
                    sidebar.style.setProperty('width', newWidth + 'px', 'important');
                    sidebar.style.setProperty('min-width', newWidth + 'px', 'important');
                    try { localStorage.setItem('sw25_sidebar_width', newWidth); } catch(err) {}
                }

                function stopDrag(e) {
                    if (isDragging) {
                        isDragging = false;
                        resizer.classList.remove('dragging');
                        try { resizer.releasePointerCapture(e.pointerId); } catch(err) {}
                        doc.removeEventListener('pointermove', onPointerMove);
                        doc.removeEventListener('pointerup', stopDrag);
                        doc.removeEventListener('pointercancel', stopDrag);
                        doc.body.style.userSelect = '';
                        doc.body.style.cursor = '';
                    }
                }

                resizer.addEventListener('pointerdown', function(e) {
                    isDragging = true;
                    startX = e.clientX;
                    startW = sidebar.getBoundingClientRect().width;
                    try { resizer.setPointerCapture(e.pointerId); } catch(err) {}
                    resizer.classList.add('dragging');
                    doc.addEventListener('pointermove', onPointerMove);
                    doc.addEventListener('pointerup', stopDrag);
                    doc.addEventListener('pointercancel', stopDrag);
                    doc.body.style.userSelect = 'none';
                    doc.body.style.cursor = 'col-resize';
                    e.preventDefault();
                    e.stopPropagation();
                });

                resizer.addEventListener('pointermove', onPointerMove);
                resizer.addEventListener('pointerup', stopDrag);
                resizer.addEventListener('pointercancel', stopDrag);

                // ダブルクリックでデフォルト幅 (460px) にリセット
                resizer.addEventListener('dblclick', function(e) {
                    const defaultW = 460;
                    doc.documentElement.style.setProperty('--sw25-sidebar-w', defaultW + 'px');
                    sidebar.style.setProperty('width', defaultW + 'px', 'important');
                    sidebar.style.setProperty('min-width', defaultW + 'px', 'important');
                    try { localStorage.setItem('sw25_sidebar_width', defaultW); } catch(err) {}
                });
            }
        }

        function fixTabScoping(sidebar) {
            // スタイル要素のみ・空コンテナの余白を強制ゼロ化 (未適用の要素のみ更新して無限発火を防止)
            const containers = sidebar.querySelectorAll('div[data-testid="stElementContainer"]');
            for (let i = 0; i < containers.length; i++) {
                const el = containers[i];
                if (el.children.length === 0 || (el.children.length === 1 && el.firstElementChild && el.firstElementChild.tagName === 'STYLE')) {
                    if (el.style.display !== 'none') {
                        el.style.setProperty('display', 'none', 'important');
                        el.style.setProperty('height', '0px', 'important');
                        el.style.setProperty('margin', '0px', 'important');
                        el.style.setProperty('padding', '0px', 'important');
                    }
                }
            }

            const tablists = sidebar.querySelectorAll('[role="tablist"], [data-baseweb="tab-list"]');
            for (let i = 0; i < tablists.length; i++) {
                const tl = tablists[i];
                const isInner = tl.closest('[role="tabpanel"], [data-testid="stTabPanel"]');
                if (!isInner) {
                    if (!tl.classList.contains('sw25-char-tablist')) {
                        tl.classList.add('sw25-char-tablist');
                        tl.classList.remove('sw25-part-tablist', 'sw25-action-tablist');
                    }
                } else {
                    const text = tl.textContent || '';
                    const targetCls = (text.includes('部位連動') || text.includes('👑') || text.includes('🔹')) ? 'sw25-part-tablist' : 'sw25-action-tablist';
                    const removeCls = targetCls === 'sw25-part-tablist' ? 'sw25-action-tablist' : 'sw25-part-tablist';
                    if (!tl.classList.contains(targetCls) || tl.classList.contains('sw25-char-tablist')) {
                        tl.classList.add(targetCls);
                        tl.classList.remove('sw25-char-tablist', removeCls);
                        tl.style.setProperty('position', 'static', 'important');
                        tl.style.setProperty('top', 'auto', 'important');
                        tl.style.setProperty('left', 'auto', 'important');
                        tl.style.setProperty('width', '100%', 'important');
                        tl.style.setProperty('height', 'auto', 'important');
                        tl.style.setProperty('z-index', '10', 'important');
                    }
                }
            }
        }

        function runAll() {
            rafScheduled = false;
            try {
                const pWin = window.parent;
                const doc = pWin.document;
                if (!doc) return;
                const sidebar = doc.querySelector('section[data-testid="stSidebar"]');
                if (!sidebar) return;

                initSidebarResizer(doc, sidebar);
                fixTabScoping(sidebar);

                if (!sidebar._sw25Observed && typeof MutationObserver !== 'undefined') {
                    sidebar._sw25Observed = true;
                    if (pWin._sw25SidebarObserver) {
                        pWin._sw25SidebarObserver.disconnect();
                    }
                    const obs = new MutationObserver(scheduleRunAll);
                    obs.observe(sidebar, { childList: true, subtree: true });
                    pWin._sw25SidebarObserver = obs;
                }
            } catch(e) {}
        }

        runAll();
        setTimeout(runAll, 50);
        setTimeout(runAll, 180);
        setTimeout(runAll, 450);
    })();
    </script>
    """
    st.components.v1.html(sidebar_resizer_js, height=0)

    # --- サイドバー (設定・準備) ---
    with st.sidebar:
        render_combined_sidebar(st.session_state.draft_pc_roster, st.session_state.draft_en_roster)
        if st.button("✨ 以上の設定を反映してリセット", type="primary", use_container_width=True):
            st.session_state.pc_party = convert_roster_to_party(st.session_state.draft_pc_roster, "PC")
            st.session_state.enemy_party = convert_roster_to_party(st.session_state.draft_en_roster, "エネミー")
            st.session_state.battle_log = ["**戦闘開始！**"]
            st.session_state.turn = 1
            st.session_state.sim_results = None


    # --- メイン画面 ---
    tab_single, tab_multi, tab_guide = st.tabs([
        "⚔️ 個別シミュレーション (ログ確認用)",
        "📊 統計シミュレーション (バランス検証用)",
        "📖 使い方・仕様解説ガイド"
    ])
    with tab_single:
        render_single_battle_tab()
    with tab_multi:
        render_multi_battle_tab()
    with tab_guide:
        render_guide_tab()

if __name__ == "__main__":
    main()
