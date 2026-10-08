#!/usr/bin/env python3
"""校验 dtbo.img 是否是合法的 Android DT table，并确认其中含目标机型的 DTB。

用法:
    check-dtbo.py <dtbo.img> [--require alioth]

背景:
    alioth(Redmi K40) 的 DTB 来自 dtbo 分区（boot.img/vendor_boot.img 都不内嵌
    alioth DTB）。只替换内核而不随之内核树编出的 dtbo.img，会因为驱动 DTS binding
    与出厂 DTB 不一致，导致关键驱动（UFS/显示/时钟）起不来，卡在开机 logo。

    因此这里做硬校验：DT table 必须可解析，且至少有一个条目的 compatible 字符串
    命中目标机型关键字，否则以非 0 退出，让构建失败而不是产出不能用的包。

DT table header（大端 uint32）:
    magic, total_size, header_size, dt_entry_size, dt_entry_count,
    dt_entries_offset, page_size, version
每个 entry:
    dt_size, dt_offset, id, rev, custom[4]
"""
import struct
import sys

DT_TABLE_MAGIC = 0xD7B7AB1E


def parse(path):
    with open(path, 'rb') as f:
        blob = f.read()
    if len(blob) < 32:
        raise SystemExit(f'[FAIL] {path} 太小（{len(blob)} 字节），不是 dtbo 镜像')

    magic, total, hsz, esz, ecnt, eoff, psz, ver = struct.unpack('>8I', blob[:32])
    print(f'  magic          = 0x{magic:08x}（期望 0x{DT_TABLE_MAGIC:08x}）')
    print(f'  version        = {ver}   page_size = {psz}')
    print(f'  total_size     = {total}   文件大小 = {len(blob)}')
    print(f'  header_size    = {hsz}   entry_size = {esz}   entries = {ecnt}')
    print(f'  entries_offset = {eoff}')

    if magic != DT_TABLE_MAGIC:
        raise SystemExit('[FAIL] dtbo 魔数不匹配，不是合法的 DT table')
    if ecnt == 0:
        raise SystemExit('[FAIL] DT table 条目数为 0')
    if ecnt > 4096:
        raise SystemExit(f'[FAIL] DT table 条目数异常（{ecnt}）')

    entries = []
    for i in range(ecnt):
        off = eoff + i * esz
        if off + 8 > len(blob):
            raise SystemExit(f'[FAIL] 条目 {i} 的偏移 {off} 越界')
        dt_size, dt_offset = struct.unpack('>II', blob[off:off + 8])
        entries.append((i, dt_size, dt_offset))

    return blob, entries


def main():
    args = [a for a in sys.argv[1:]]
    require = 'alioth'
    if '--require' in args:
        k = args.index('--require')
        require = args[k + 1]
        del args[k:k + 2]
    if len(args) != 1:
        raise SystemExit(__doc__)
    path = args[0]

    print(f'== 解析 dtbo: {path} ==')
    blob, entries = parse(path)

    hits = []
    print('  idx   dt_size   dt_offset  compatible 命中')
    for idx, dt_size, dt_offset in entries:
        seg = blob[dt_offset:dt_offset + dt_size]
        if seg[:4] != b'\xd0\x0d\xfe\xed':
            mark = 'BAD-MAGIC'
        elif require.encode() in seg:
            mark = f'<== 含 {require!r}'
            hits.append(idx)
        else:
            mark = '.'
        print(f'  {idx:4d}  {dt_size:8d}  {dt_offset:9d}  {mark}')

    print(f'== 共 {len(entries)} 个 DTB，命中 {require!r} 的条目: {len(hits)} 个 {hits} ==')
    if not hits:
        raise SystemExit(f'[FAIL] 没有任何 DTB 的 compatible 命中 {require!r} —— 该 dtbo 不能用于该机型')
    print('[OK] dtbo.img 校验通过')


if __name__ == '__main__':
    main()
