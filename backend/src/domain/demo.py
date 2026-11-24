"""澄岭精密的固定演练台账、证书历史、产线与检验方案。"""

from .spec import ACTIVE, CERTIFICATE, DEMO_DAY, INACTIVE, INSTRUMENT, LINE, PLAN, PLAN_ITEM


def demo_rows() -> list[dict]:
    instruments = [
        ('QC-101', '数显卡尺', '机加工区', '车削一班', ACTIVE),
        ('QC-102', '扭矩扳手', '装配区', '装配二班', ACTIVE),
        ('QC-103', '外径千分尺', '机加工区', '磨削班', ACTIVE),
        ('QC-104', '针规组', '终检区', '终检班', ACTIVE),
        ('QC-105', '电子天平', '终检区', '来料检验班', ACTIVE),
        ('QC-106', '高度规', '终检区', '终检班', ACTIVE),
        ('QC-107', '指针式压力表', '装配区', '装配二班', INACTIVE),
    ]
    certificates = [
        ('QC-103', 'CL-103-26', '2026-06-05', '2026-12-05'),
        ('QC-101', 'CL-101-27', '2026-11-22', '2027-05-22'),
        ('QC-105', 'CL-105-26', DEMO_DAY, '2027-05-21'),
        ('QC-101', 'CL-101-26', '2026-05-20', '2026-11-20'),
        ('QC-101', 'CL-101-25', '2025-11-20', '2026-05-20'),
        ('QC-102', 'CL-102-26', '2026-05-21', DEMO_DAY),
        ('QC-104', 'CL-104-26', '2026-06-06', '2026-12-06'),
        ('QC-106', 'CL-106-26', '2026-11-22', '2027-05-22'),
        ('QC-107', 'CL-107-25', '2025-11-05', '2026-05-05'),
    ]
    lines = [
        ('L-MACH', '机加工线'),
        ('L-ASSY', '装配线'),
        ('L-PACK', '包装线'),
        ('L-FINAL', '终检线'),
    ]
    plans = [
        ('P-01', '外径卡尺检验', 'L-MACH'),
        ('P-02', '千分尺复检', 'L-MACH'),
        ('P-03', '卡尺与针规联检', 'L-MACH'),
        ('P-04', '扭矩装配检验', 'L-ASSY'),
        ('P-05', '压力表装配检验', 'L-ASSY'),
        ('P-06', '扭矩复检', 'L-ASSY'),
        ('P-07', '包装称重检验', 'L-PACK'),
        ('P-08', '包装封口检验', 'L-PACK'),
        ('P-09', '终检高度检验', 'L-FINAL'),
    ]
    plan_items = [
        ('P-01', 'QC-101'), ('P-01', 'QC-103'),
        ('P-02', 'QC-103'), ('P-02', 'QC-104'),
        ('P-03', 'QC-101'), ('P-03', 'QC-104'),
        ('P-04', 'QC-102'), ('P-04', 'QC-105'),
        ('P-05', 'QC-105'), ('P-05', 'QC-107'),
        ('P-06', 'QC-102'), ('P-06', 'QC-103'),
        ('P-07', 'QC-107'), ('P-07', 'QC-105'),
        ('P-08', 'QC-107'),
        ('P-09', 'QC-106'), ('P-09', 'QC-104'),
    ]
    return [
        dict(record_type=INSTRUMENT, asset_id=asset_id, asset_name=name, work_area=area,
             owner=owner, asset_status=status, certificate_id=None, issued_on=None, expires_on=None,
             line_code=None, line_name=None, plan_code=None, plan_name=None)
        for asset_id, name, area, owner, status in instruments
    ] + [
        dict(record_type=CERTIFICATE, asset_id=asset_id, asset_name=None, work_area=None,
             owner=None, asset_status=None, certificate_id=code, issued_on=issued, expires_on=expires,
             line_code=None, line_name=None, plan_code=None, plan_name=None)
        for asset_id, code, issued, expires in certificates
    ] + [
        dict(record_type=LINE, asset_id=None, asset_name=None, work_area=None, owner=None,
             asset_status=None, certificate_id=None, issued_on=None, expires_on=None,
             line_code=code, line_name=name, plan_code=None, plan_name=None)
        for code, name in lines
    ] + [
        dict(record_type=PLAN, asset_id=None, asset_name=None, work_area=None, owner=None,
             asset_status=None, certificate_id=None, issued_on=None, expires_on=None,
             line_code=line_code, line_name=None, plan_code=code, plan_name=name)
        for code, name, line_code in plans
    ] + [
        dict(record_type=PLAN_ITEM, asset_id=asset_id, asset_name=None, work_area=None, owner=None,
             asset_status=None, certificate_id=None, issued_on=None, expires_on=None,
             line_code=None, line_name=None, plan_code=code, plan_name=None)
        for code, asset_id in plan_items
    ]
