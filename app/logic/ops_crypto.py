"""OPS 固件解密模块
基于开源项目 opscrypto.py (c) B.Kerler 2019-2022, MIT License
https://github.com/bkerler/oppo_decrypt

一加 .ops 固件使用自定义 SM4-like 加密算法，本模块实现了完整的解密逻辑。
"""
import os
import hashlib
import mmap
import xml.etree.ElementTree as ET
from struct import unpack
from threading import Event
from typing import Callable, Optional


def _mmap_io(filename, mode, length=0):
    """内存映射文件读写"""
    if mode == "rb":
        with open(filename, mode="rb") as file_obj:
            return mmap.mmap(file_obj.fileno(), length=0, access=mmap.ACCESS_READ)
    elif mode == "wb":
        if os.path.exists(filename):
            length = os.stat(filename).st_size
        else:
            with open(filename, "wb") as wf:
                wf.write(length * b'\x00')
        with open(filename, mode="r+b") as file_obj:
            return mmap.mmap(file_obj.fileno(), length=length, access=mmap.ACCESS_WRITE)


# 主密钥
key = unpack("<4I", bytes.fromhex("d1b5e39e5eea049d671dd5abd2afcbaf"))

# guacamoles_31_O.09_190820
mbox5 = [0x60, 0x8a, 0x3f, 0x2d, 0x68, 0x6b, 0xd4, 0x23, 0x51, 0x0c,
         0xd0, 0x95, 0xbb, 0x40, 0xe9, 0x76, 0x00, 0x00, 0x00, 0x00,
         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
         0x0a, 0x00]
# instantnoodlev_15_O.07_201103
mbox6 = [0xAA, 0x69, 0x82, 0x9E, 0x5D, 0xDE, 0xB1, 0x3D, 0x30, 0xBB,
         0x81, 0xA3, 0x46, 0x65, 0xa3, 0xe1, 0x00, 0x00, 0x00, 0x00,
         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
         0x0a, 0x00]
# guacamolet_21_O.08_190502
mbox4 = [0xC4, 0x5D, 0x05, 0x71, 0x99, 0xDD, 0xBB, 0xEE, 0x29, 0xA1,
         0x6D, 0xC7, 0xAD, 0xBF, 0xA4, 0x3F, 0x00, 0x00, 0x00, 0x00,
         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
         0x0a, 0x00]

sbox = bytes.fromhex("c66363a5c66363a5f87c7c84f87c7c84ee777799ee777799f67b7b8df67b7b8d" +
                     "fff2f20dfff2f20dd66b6bbdd66b6bbdde6f6fb1de6f6fb191c5c55491c5c554" +
                     "60303050603030500201010302010103ce6767a9ce6767a9562b2b7d562b2b7d" +
                     "e7fefe19e7fefe19b5d7d762b5d7d7624dababe64dababe6ec76769aec76769a" +
                     "8fcaca458fcaca451f82829d1f82829d89c9c94089c9c940fa7d7d87fa7d7d87" +
                     "effafa15effafa15b25959ebb25959eb8e4747c98e4747c9fbf0f00bfbf0f00b" +
                     "41adadec41adadecb3d4d467b3d4d4675fa2a2fd5fa2a2fd45afafea45afafea" +
                     "239c9cbf239c9cbf53a4a4f753a4a4f7e4727296e47272969bc0c05b9bc0c05b" +
                     "75b7b7c275b7b7c2e1fdfd1ce1fdfd1c3d9393ae3d9393ae4c26266a4c26266a" +
                     "6c36365a6c36365a7e3f3f417e3f3f41f5f7f702f5f7f70283cccc4f83cccc4f" +
                     "6834345c6834345c51a5a5f451a5a5f4d1e5e534d1e5e534f9f1f108f9f1f108" +
                     "e2717193e2717193abd8d873abd8d87362313153623131532a15153f2a15153f" +
                     "0804040c0804040c95c7c75295c7c75246232365462323659dc3c35e9dc3c35e" +
                     "3018182830181828379696a1379696a10a05050f0a05050f2f9a9ab52f9a9ab5" +
                     "0e0707090e07070924121236241212361b80809b1b80809bdfe2e23ddfe2e23d" +
                     "cdebeb26cdebeb264e2727694e2727697fb2b2cd7fb2b2cdea75759fea75759f" +
                     "1209091b1209091b1d83839e1d83839e582c2c74582c2c74341a1a2e341a1a2e" +
                     "361b1b2d361b1b2ddc6e6eb2dc6e6eb2b45a5aeeb45a5aee5ba0a0fb5ba0a0fb" +
                     "a45252f6a45252f6763b3b4d763b3b4db7d6d661b7d6d6617db3b3ce7db3b3ce" +
                     "5229297b5229297bdde3e33edde3e33e5e2f2f715e2f2f711384849713848497" +
                     "a65353f5a65353f5b9d1d168b9d1d1680000000000000000c1eded2cc1eded2c" +
                     "4020206040202060e3fcfc1fe3fcfc1f79b1b1c879b1b1c8b65b5bedb65b5bed" +
                     "d46a6abed46a6abe8dcbcb468dcbcb4667bebed967bebed97239394b7239394b" +
                     "944a4ade944a4ade984c4cd4984c4cd4b05858e8b05858e885cfcf4a85cfcf4a" +
                     "bbd0d06bbbd0d06bc5efef2ac5efef2a4faaaae54faaaae5edfbfb16edfbfb16" +
                     "864343c5864343c59a4d4dd79a4d4dd766333355663333551185859411858594" +
                     "8a4545cf8a4545cfe9f9f910e9f9f9100402020604020206fe7f7f81fe7f7f81" +
                     "a05050f0a05050f0783c3c44783c3c44259f9fba259f9fba4ba8a8e34ba8a8e3" +
                     "a25151f3a25151f35da3a3fe5da3a3fe804040c0804040c0058f8f8a058f8f8a" +
                     "3f9292ad3f9292ad219d9dbc219d9dbc7038384870383848f1f5f504f1f5f504" +
                     "63bcbcdf63bcbcdf77b6b6c177b6b6c1afdada75afdada754221216342212163" +
                     "2010103020101030e5ffff1ae5ffff1afdf3f30efdf3f30ebfd2d26dbfd2d26d" +
                     "81cdcd4c81cdcd4c180c0c14180c0c142613133526131335c3ecec2fc3ecec2f" +
                     "be5f5fe1be5f5fe1359797a2359797a2884444cc884444cc2e1717392e171739" +
                     "93c4c45793c4c45755a7a7f255a7a7f2fc7e7e82fc7e7e827a3d3d477a3d3d47" +
                     "c86464acc86464acba5d5de7ba5d5de73219192b3219192be6737395e6737395" +
                     "c06060a0c06060a019818198198181989e4f4fd19e4f4fd1a3dcdc7fa3dcdc7f" +
                     "4422226644222266542a2a7e542a2a7e3b9090ab3b9090ab0b8888830b888883" +
                     "8c4646ca8c4646cac7eeee29c7eeee296bb8b8d36bb8b8d32814143c2814143c" +
                     "a7dede79a7dede79bc5e5ee2bc5e5ee2160b0b1d160b0b1daddbdb76addbdb76" +
                     "dbe0e03bdbe0e03b6432325664323256743a3a4e743a3a4e140a0a1e140a0a1e" +
                     "924949db924949db0c06060a0c06060a4824246c4824246cb85c5ce4b85c5ce4" +
                     "9fc2c25d9fc2c25dbdd3d36ebdd3d36e43acacef43acacefc46262a6c46262a6" +
                     "399191a8399191a8319595a4319595a4d3e4e437d3e4e437f279798bf279798b" +
                     "d5e7e732d5e7e7328bc8c8438bc8c8436e3737596e373759da6d6db7da6d6db7" +
                     "018d8d8c018d8d8cb1d5d564b1d5d5649c4e4ed29c4e4ed249a9a9e049a9a9e0" +
                     "d86c6cb4d86c6cb4ac5656faac5656faf3f4f407f3f4f407cfeaea25cfeaea25" +
                     "ca6565afca6565aff47a7a8ef47a7a8e47aeaee947aeaee91008081810080818" +
                     "6fbabad56fbabad5f0787888f07878884a25256f4a25256f5c2e2e725c2e2e72" +
                     "381c1c24381c1c2457a6a6f157a6a6f173b4b4c773b4b4c797c6c65197c6c651" +
                     "cbe8e823cbe8e823a1dddd7ca1dddd7ce874749ce874749c3e1f1f213e1f1f21" +
                     "964b4bdd964b4bdd61bdbddc61bdbddc0d8b8b860d8b8b860f8a8a850f8a8a85" +
                     "e0707090e07070907c3e3e427c3e3e4271b5b5c471b5b5c4cc6666aacc6666aa" +
                     "904848d8904848d80603030506030305f7f6f601f7f6f6011c0e0e121c0e0e12" +
                     "c26161a3c26161a36a35355f6a35355fae5757f9ae5757f969b9b9d069b9b9d0" +
                     "178686911786869199c1c15899c1c1583a1d1d273a1d1d27279e9eb9279e9eb9" +
                     "d9e1e138d9e1e138ebf8f813ebf8f8132b9898b32b9898b32211113322111133" +
                     "d26969bbd26969bba9d9d970a9d9d970078e8e89078e8e89339494a7339494a7" +
                     "2d9b9bb62d9b9bb63c1e1e223c1e1e221587879215878792c9e9e920c9e9e920" +
                     "87cece4987cece49aa5555ffaa5555ff5028287850282878a5dfdf7aa5dfdf7a" +
                     "038c8c8f038c8c8f59a1a1f859a1a1f809898980098989801a0d0d171a0d0d17" +
                     "65bfbfda65bfbfdad7e6e631d7e6e631844242c6844242c6d06868b8d06868b8" +
                     "824141c3824141c3299999b0299999b05a2d2d775a2d2d771e0f0f111e0f0f11" +
                     "7bb0b0cb7bb0b0cba85454fca85454fc6dbbbbd66dbbbbd62c16163a2c16163a")


def gsbox(offset):
    return int.from_bytes(sbox[offset:offset + 4], 'little')


def key_update(iv1, asbox):
    d = iv1[0] ^ asbox[0]
    a = iv1[1] ^ asbox[1]
    b = iv1[2] ^ asbox[2]
    c = iv1[3] ^ asbox[3]
    e = gsbox(((b >> 0x10) & 0xff) * 8 + 2) ^ gsbox(((a >> 8) & 0xff) * 8 + 3) ^ gsbox((c >> 0x18) * 8 + 1) ^ \
        gsbox((d & 0xff) * 8) ^ asbox[4]
    h = gsbox(((c >> 0x10) & 0xff) * 8 + 2) ^ gsbox(((b >> 8) & 0xff) * 8 + 3) ^ gsbox((d >> 0x18) * 8 + 1) ^ \
        gsbox((a & 0xff) * 8) ^ asbox[5]
    i = gsbox(((d >> 0x10) & 0xff) * 8 + 2) ^ gsbox(((c >> 8) & 0xff) * 8 + 3) ^ gsbox((a >> 0x18) * 8 + 1) ^ \
        gsbox((b & 0xff) * 8) ^ asbox[6]
    a = gsbox(((d >> 8) & 0xff) * 8 + 3) ^ gsbox(((a >> 0x10) & 0xff) * 8 + 2) ^ gsbox((b >> 0x18) * 8 + 1) ^ \
        gsbox((c & 0xff) * 8) ^ asbox[7]
    g = 8
    for f in range(asbox[0x3c] - 2):
        d = e >> 0x18
        m = h >> 0x10
        s = h >> 0x18
        z = e >> 0x10
        l = i >> 0x18
        t = e >> 8
        e = gsbox(((i >> 0x10) & 0xff) * 8 + 2) ^ gsbox(((h >> 8) & 0xff) * 8 + 3) ^ \
            gsbox((a >> 0x18) * 8 + 1) ^ gsbox((e & 0xff) * 8) ^ asbox[g]
        h = gsbox(((a >> 0x10) & 0xff) * 8 + 2) ^ gsbox(((i >> 8) & 0xff) * 8 + 3) ^ \
            gsbox(d * 8 + 1) ^ gsbox((h & 0xff) * 8) ^ asbox[g + 1]
        i = gsbox((z & 0xff) * 8 + 2) ^ gsbox(((a >> 8) & 0xff) * 8 + 3) ^ \
            gsbox(s * 8 + 1) ^ gsbox((i & 0xff) * 8) ^ asbox[g + 2]
        a = gsbox((t & 0xff) * 8 + 3) ^ gsbox((m & 0xff) * 8 + 2) ^ \
            gsbox(l * 8 + 1) ^ gsbox((a & 0xff) * 8) ^ asbox[g + 3]
        g = g + 4
    return [(gsbox(((i >> 0x10) & 0xff) * 8) & 0xff0000) ^ (gsbox(((h >> 8) & 0xff) * 8 + 1) & 0xff00) ^
            (gsbox((a >> 0x18) * 8 + 3) & 0xff000000) ^ gsbox((e & 0xff) * 8 + 2) & 0xFF ^ asbox[g],
            (gsbox(((a >> 0x10) & 0xff) * 8) & 0xff0000) ^ (gsbox(((i >> 8) & 0xff) * 8 + 1) & 0xff00) ^
            (gsbox((e >> 0x18) * 8 + 3) & 0xff000000) ^ (gsbox((h & 0xff) * 8 + 2) & 0xFF) ^ asbox[g + 3],
            (gsbox(((e >> 0x10) & 0xff) * 8) & 0xff0000) ^ (gsbox(((a >> 8) & 0xff) * 8 + 1) & 0xff00) ^
            (gsbox((h >> 0x18) * 8 + 3) & 0xff000000) ^ (gsbox((i & 0xff) * 8 + 2) & 0xFF) ^ asbox[g + 2],
            (gsbox(((h >> 0x10) & 0xff) * 8) & 0xff0000) ^ (gsbox(((e >> 8) & 0xff) * 8 + 1) & 0xff00) ^
            (gsbox((i >> 0x18) * 8 + 3) & 0xff000000) ^ (gsbox((a & 0xff) * 8 + 2) & 0xFF) ^ asbox[g + 1]]


def key_custom(inp, rkey, outlength=0, encrypt=False):
    outp = bytearray()
    inp = bytearray(inp)
    pos = outlength
    outp_extend = outp.extend
    ptr = 0
    length = len(inp)
    if outlength != 0:
        while pos < len(rkey):
            if length == 0:
                break
            buffer = inp[pos]
            outp_extend(rkey[pos] ^ buffer)
            rkey[pos] = buffer
            length -= 1
            pos += 1
    if length > 0xF:
        for ptr in range(0, length, 0x10):
            rkey = key_update(rkey, mbox)
            if pos < 0x10:
                slen = ((0xf - pos) >> 2) + 1
                tmp = [rkey[i] ^ int.from_bytes(inp[pos + (i * 4) + ptr:pos + (i * 4) + ptr + 4], "little") for i in range(0, slen)]
                outp.extend(b"".join(tmp[i].to_bytes(4, 'little') for i in range(0, slen)))
                if encrypt:
                    rkey = tmp
                else:
                    rkey = [int.from_bytes(inp[pos + (i * 4) + ptr:pos + (i * 4) + ptr + 4], "little") for i in range(0, slen)]
            length = length - 0x10
    if length != 0:
        rkey = key_update(rkey, sbox)
        j = pos
        m = 0
        while length > 0:
            data = inp[j + ptr:j + ptr + 4]
            if len(data) < 4:
                data += b"\x00" * (4 - len(data))
            tmp = int.from_bytes(data, 'little')
            outp_extend((tmp ^ rkey[m]).to_bytes(4, 'little'))
            if encrypt:
                rkey[m] = tmp ^ rkey[m]
            else:
                rkey[m] = tmp
            length -= 4
            j += 4
            m += 1
    return outp


def _extractxml(filename, key, mbox, path):
    """从 OPS 文件尾部解密 settings.xml"""
    with _mmap_io(filename, 'rb') as rf:
        sfilename = os.path.join(path, "settings.xml")
        filesize = os.stat(filename).st_size
        rf.seek(filesize - 0x200)
        hdr = rf.read(0x200)
        xmllength = int.from_bytes(hdr[0x18:0x18 + 4], 'little')
        xmlpad = 0x200 - (xmllength % 0x200)
        rf.seek(filesize - 0x200 - (xmllength + xmlpad))
        inp = rf.read(xmllength + xmlpad)
        outp = key_custom(inp, key, 0)
        if b"xml " not in outp:
            return None
        with _mmap_io(sfilename, 'wb', xmllength) as wf:
            wf.write(outp[:xmllength])
        return outp[:xmllength].decode('utf-8')


def _decryptfile(rkey, filename, path, wfilename, start, length):
    """解密单个文件"""
    sha256 = hashlib.sha256()
    with _mmap_io(filename, 'rb') as rf:
        rf.seek(start)
        data = rf.read(length)
        if length % 4:
            data += (4 - (length % 4)) * b'\x00'
        outp = key_custom(data, rkey, 0)
        sha256.update(outp[:length])
        with _mmap_io(os.path.join(path, wfilename), 'wb', length) as wf:
            wf.write(outp[:length])
    if length % 0x1000 > 0:
        sha256.update(b"\x00" * (0x1000 - (length % 0x1000)))
    return sha256.hexdigest()


def _copysub(rf, wf, start, length):
    rf.seek(start)
    rlen = 0
    while length > 0:
        size = min(length, 0x100000)
        data = rf.read(size)
        wf.write(data)
        rlen += len(data)
        length -= size
    return rlen


def _copyfile(filename, path, wfilename, start, length):
    """直接拷贝（明文区）"""
    with _mmap_io(filename, 'rb') as rf:
        with _mmap_io(os.path.join(path, wfilename), 'wb', length) as wf:
            return _copysub(rf, wf, start, length)


def _calc_digest(filename):
    with _mmap_io(filename, 'rb') as rf:
        data = rf.read()
        sha256 = hashlib.sha256()
        sha256.update(data)
        if len(data) % 0x1000 > 0:
            sha256.update(b"\x00" * (0x1000 - (len(data) % 0x1000)))
    return sha256.hexdigest()


def extract_ops(
    source_path: str,
    out_dir: str,
    *,
    log_callback: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[Event] = None,
) -> bool:
    """解密并提取一加 .ops 固件文件中的所有分区镜像。

    - source_path: .ops 固件文件路径
    - out_dir: 输出目录
    - log_callback: 日志回调函数
    - cancel_event: 取消事件

    返回: True 表示成功, False 表示失败或取消
    """
    if log_callback is None:
        def log_callback(_msg: str):
            return
    if cancel_event is None:
        cancel_event = Event()

    global mbox
    log_callback(f"源文件: {source_path}")
    log_callback(f"输出目录: {out_dir}")
    log_callback("")

    if not os.path.exists(source_path):
        log_callback(f"错误: 文件不存在 - {source_path}")
        return False

    os.makedirs(out_dir, exist_ok=True)

    # 尝试 mbox5 -> mbox6 -> mbox4 顺序探测密钥
    mbox_list = [(mbox5, "MBox5"), (mbox6, "MBox6"), (mbox4, "MBox4")]
    xml = None
    mbox = None
    for mb, name in mbox_list:
        if cancel_event.is_set():
            log_callback("用户取消操作")
            return False
        mbox = mb
        log_callback(f"尝试密钥: {name} ...")
        try:
            xml = _extractxml(source_path, key, mbox, out_dir)
        except Exception as e:
            log_callback(f"  {name} 失败: {e}")
            xml = None
        if xml is not None:
            log_callback(f"✅ 密钥匹配: {name}")
            break
        else:
            log_callback(f"  {name} 不匹配")

    if xml is None:
        log_callback("错误: 所有密钥均不匹配，可能是不支持的 OPS 版本")
        return False

    log_callback("")
    log_callback("解析 settings.xml ...")
    root = ET.fromstring(xml)
    file_count = 0

    for child in root:
        if cancel_event.is_set():
            log_callback("用户取消操作")
            return False

        if child.tag == "SAHARA":
            for item in child:
                if item.tag == "File":
                    wfilename = item.attrib["Path"]
                    start = int(item.attrib["FileOffsetInSrc"]) * 0x200
                    length = int(item.attrib["SizeInByteInSrc"])
                    log_callback(f"解密: {wfilename} ({length} bytes)")
                    try:
                        _decryptfile(key, source_path, out_dir, wfilename, start, length)
                        file_count += 1
                    except Exception as e:
                        log_callback(f"  ❌ 失败: {e}")

        elif child.tag == "UFS_PROVISION":
            for item in child:
                if item.tag == "File":
                    wfilename = item.attrib["Path"]
                    start = int(item.attrib["FileOffsetInSrc"]) * 0x200
                    length = int(item.attrib["SizeInByteInSrc"])
                    log_callback(f"提取: {wfilename} ({length} bytes)")
                    try:
                        _copyfile(source_path, out_dir, wfilename, start, length)
                        file_count += 1
                    except Exception as e:
                        log_callback(f"  ❌ 失败: {e}")

        elif "Program" in child.tag:
            for item in child:
                if "filename" in item.attrib:
                    wfilename = item.attrib["filename"]
                    if wfilename == "":
                        continue
                    start = int(item.attrib["FileOffsetInSrc"]) * 0x200
                    length = int(item.attrib["SizeInByteInSrc"])
                    sha256 = item.attrib.get("Sha256", "")
                    sparse = item.attrib.get("sparse", "false") == "true"
                    log_callback(f"提取: {wfilename} ({length} bytes)")
                    try:
                        _copyfile(source_path, out_dir, wfilename, start, length)
                        file_count += 1
                        if sha256 and not sparse:
                            csha256 = _calc_digest(os.path.join(out_dir, wfilename))
                            if sha256 != csha256:
                                log_callback(f"  ⚠️ Sha256 校验不一致 (sparse={sparse})")
                    except Exception as e:
                        log_callback(f"  ❌ 失败: {e}")
                else:
                    for subitem in item:
                        if "filename" in subitem.attrib:
                            wfilename = subitem.attrib["filename"]
                            if wfilename == "":
                                continue
                            start = int(subitem.attrib["FileOffsetInSrc"]) * 0x200
                            length = int(subitem.attrib["SizeInByteInSrc"])
                            sha256 = subitem.attrib.get("Sha256", "")
                            sparse = subitem.attrib.get("sparse", "false") == "true"
                            log_callback(f"提取: {wfilename} ({length} bytes)")
                            try:
                                _copyfile(source_path, out_dir, wfilename, start, length)
                                file_count += 1
                                if sha256 and not sparse:
                                    csha256 = _calc_digest(os.path.join(out_dir, wfilename))
                                    if sha256 != csha256:
                                        log_callback(f"  ⚠️ Sha256 校验不一致 (sparse={sparse})")
                            except Exception as e:
                                log_callback(f"  ❌ 失败: {e}")

    log_callback("")
    log_callback(f"✅ 解包完成，共提取 {file_count} 个文件到 {out_dir}")
    return not cancel_event.is_set()
