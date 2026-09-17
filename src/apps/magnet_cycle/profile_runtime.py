from dataclasses import dataclass
from half_linac.src.shared.machine_profile.limits import LimitRange
from half_linac.src.shared.machine_profile import resolve_write_target, resolve_channel

KINDS = {'bend': '二极铁', 'quad': '四极铁', 'corr': '校正磁铁', 'solenoid': '螺线管'}


@dataclass(frozen=True)
class Magnet:
    name: str
    kind: str
    low: float | None = None
    high: float | None = None
    set_pv: str = ''
    read_pv: str = ''
    error: str = ''


def load_magnets(context, simulate=False):
    result = []
    for element in context.profile.elements:
        if element.kind not in KINDS:
            continue
        try:
            limits = LimitRange.from_mapping(element.limits_for('current_set'))
            if limits.low is None or limits.high is None:
                raise ValueError('缺少完整电流 limits')
            if limits.unit and limits.unit.casefold() != 'a':
                raise ValueError('电流 limits 单位必须为 A')
            if simulate:
                set_pv, read_pv = f'sim:{element.id}:set', f'sim:{element.id}:read'
            else:
                target = resolve_write_target(context, element.id, quantity='current')
                set_pv = target.pv_name
                read_pv = resolve_channel(context, element.id, 'current_readback')
            result.append(Magnet(element.id, element.kind, limits.low, limits.high, set_pv, read_pv))
        except (ValueError, KeyError) as exc:
            result.append(Magnet(element.id, element.kind, error=str(exc)))
    return result


def group_magnets(magnets):
    groups = {}
    for magnet in magnets:
        if magnet.error:
            raise ValueError(magnet.error)
        group = groups.setdefault(magnet.set_pv, [])
        if group and (group[0].low, group[0].high, group[0].read_pv) != (magnet.low, magnet.high, magnet.read_pv):
            raise ValueError(f'{magnet.name}: 共用设定 PV 的 limits 或读回配置不一致')
        group.append(magnet)
    return list(groups.values())
