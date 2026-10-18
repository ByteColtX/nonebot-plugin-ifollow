"""抖音 web 接口的 a_bogus 请求签名。

算法移植自 f2 项目的 ``f2/utils/abogus.py``（Apache License 2.0，
版权归 Johnserf-Seed 所有，https://github.com/Johnserf-Seed/f2 ），
是抖音网页端 JS 签名逻辑的等价实现。抖音调整算法时需要同步上游实现，
因此这里保持与原实现的数值和流程一致，不做算法层面的"优化"。

抖音的 ``aweme/v1/web`` 系列接口要求查询串带 ``a_bogus`` 参数，且签名绑定了
请求的 User-Agent 与浏览器指纹；接口契约见 ``douyin-fetch`` 规范。
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterable

from gmssl import func, sm3

_JS_UINT32 = 4294967296
"""JavaScript 无符号 32 位整数的模。"""

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)

_BIG_ARRAY = [
    121,
    243,
    55,
    234,
    103,
    36,
    47,
    228,
    30,
    231,
    106,
    6,
    115,
    95,
    78,
    101,
    250,
    207,
    198,
    50,
    139,
    227,
    220,
    105,
    97,
    143,
    34,
    28,
    194,
    215,
    18,
    100,
    159,
    160,
    43,
    8,
    169,
    217,
    180,
    120,
    247,
    45,
    90,
    11,
    27,
    197,
    46,
    3,
    84,
    72,
    5,
    68,
    62,
    56,
    221,
    75,
    144,
    79,
    73,
    161,
    178,
    81,
    64,
    187,
    134,
    117,
    186,
    118,
    16,
    241,
    130,
    71,
    89,
    147,
    122,
    129,
    65,
    40,
    88,
    150,
    110,
    219,
    199,
    255,
    181,
    254,
    48,
    4,
    195,
    248,
    208,
    32,
    116,
    167,
    69,
    201,
    17,
    124,
    125,
    104,
    96,
    83,
    80,
    127,
    236,
    108,
    154,
    126,
    204,
    15,
    20,
    135,
    112,
    158,
    13,
    1,
    188,
    164,
    210,
    237,
    222,
    98,
    212,
    77,
    253,
    42,
    170,
    202,
    26,
    22,
    29,
    182,
    251,
    10,
    173,
    152,
    58,
    138,
    54,
    141,
    185,
    33,
    157,
    31,
    252,
    132,
    233,
    235,
    102,
    196,
    191,
    223,
    240,
    148,
    39,
    123,
    92,
    82,
    128,
    109,
    57,
    24,
    38,
    113,
    209,
    245,
    2,
    119,
    153,
    229,
    189,
    214,
    230,
    174,
    232,
    63,
    52,
    205,
    86,
    140,
    66,
    175,
    111,
    171,
    246,
    133,
    238,
    193,
    99,
    60,
    74,
    91,
    225,
    51,
    76,
    37,
    145,
    211,
    166,
    151,
    213,
    206,
    0,
    200,
    244,
    176,
    218,
    44,
    184,
    172,
    49,
    216,
    93,
    168,
    53,
    21,
    183,
    41,
    67,
    85,
    224,
    155,
    226,
    242,
    87,
    177,
    146,
    70,
    190,
    12,
    162,
    19,
    137,
    114,
    25,
    165,
    163,
    192,
    23,
    59,
    9,
    94,
    179,
    107,
    35,
    7,
    142,
    131,
    239,
    203,
    149,
    136,
    61,
    249,
    14,
    156,
]
"""混淆用的 256 字节置换表，与抖音 JS 实现一致。"""

_ALPHABET = "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe"
_ALPHABET_ALT = "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe"
_ALPHABETS = [_ALPHABET, _ALPHABET_ALT]
_UA_KEY = b"\x00\x01\x0e"

_SORT_INDEX = [
    18,
    20,
    52,
    26,
    30,
    34,
    58,
    38,
    40,
    53,
    42,
    21,
    27,
    54,
    55,
    31,
    35,
    57,
    39,
    41,
    43,
    22,
    28,
    32,
    60,
    36,
    23,
    29,
    33,
    37,
    44,
    45,
    59,
    46,
    47,
    48,
    49,
    50,
    24,
    25,
    65,
    66,
    70,
    71,
]
_SORT_INDEX_2 = [
    18,
    20,
    26,
    30,
    34,
    38,
    40,
    42,
    21,
    27,
    31,
    35,
    39,
    41,
    43,
    22,
    28,
    32,
    36,
    23,
    29,
    33,
    37,
    44,
    45,
    46,
    47,
    48,
    49,
    50,
    24,
    25,
    52,
    53,
    54,
    55,
    57,
    58,
    59,
    60,
    65,
    66,
    70,
    71,
]


def _chars_to_codes(text: str) -> list[int]:
    """把字符串逐字符转换为码点列表。"""

    return [ord(char) for char in text]


def _codes_to_chars(codes: Iterable[int]) -> str:
    """把码点序列转换为字符串。"""

    return "".join(chr(code) for code in codes)


def _js_shift_right(value: int, bits: int) -> int:
    """复刻 JavaScript 的无符号右移运算。"""

    return value % _JS_UINT32 >> bits


def _random_bytes(length: int = 3) -> str:
    """生成抖音签名前缀使用的伪随机字节串。"""

    def generate_byte_sequence() -> list[str]:
        seed = int(random.random() * 10000)
        return [
            chr(seed & 255 & 170 | 1),
            chr(seed & 255 & 85 | 2),
            chr(_js_shift_right(seed, 8) & 170 | 5),
            chr(_js_shift_right(seed, 8) & 85 | 40),
        ]

    result: list[str] = []
    for _ in range(length):
        result.extend(generate_byte_sequence())
    return "".join(result)


class _CryptoUtility:
    """签名使用的 SM3、RC4 与自定义 Base64 工具集。"""

    def __init__(self, salt: str, alphabets: list[str]) -> None:
        """初始化工具集。

        Args:
            salt: 参与 SM3 计算的盐值。
            alphabets: 自定义 Base64 字符表。
        """

        self.salt = salt
        self.alphabets = alphabets
        self.big_array = list(_BIG_ARRAY)

    @staticmethod
    def sm3_to_array(payload: str | list[int]) -> list[int]:
        """计算 SM3 摘要并按字节展开成整数列表。"""

        if isinstance(payload, str):
            data = payload.encode("utf-8")
        else:
            data = bytes(payload)
        digest = sm3.sm3_hash(func.bytes_to_list(data))
        return [int(digest[index : index + 2], 16) for index in range(0, 64, 2)]

    def params_to_array(
        self, param: str | list[int], *, add_salt: bool = True
    ) -> list[int]:
        """按需加盐后计算参数的 SM3 摘要。"""

        if isinstance(param, str) and add_salt:
            param = param + self.salt
        return self.sm3_to_array(param)

    def transform_bytes(self, values: list[int]) -> str:
        """用置换表对字节列表做异或混淆，返回混淆后的字符串。"""

        text = _codes_to_chars(values)
        result: list[str] = []
        index_b = self.big_array[1]
        initial_value = 0
        value_e = 0

        for index, char in enumerate(text):
            if index == 0:
                initial_value = self.big_array[index_b]
                sum_initial = index_b + initial_value
                self.big_array[1] = initial_value
                self.big_array[index_b] = index_b
            else:
                sum_initial = initial_value + value_e

            sum_initial %= len(self.big_array)
            value_f = self.big_array[sum_initial]
            result.append(chr(ord(char) ^ value_f))

            value_e = self.big_array[(index + 2) % len(self.big_array)]
            sum_initial = (index_b + value_e) % len(self.big_array)
            initial_value = self.big_array[sum_initial]
            self.big_array[sum_initial] = self.big_array[
                (index + 2) % len(self.big_array)
            ]
            self.big_array[(index + 2) % len(self.big_array)] = initial_value
            index_b = sum_initial

        return "".join(result)

    def base64_encode(self, text: str, alphabet_index: int = 0) -> str:
        """用自定义字符表做 6 位分组的 Base64 编码。"""

        binary = "".join([f"{ord(char):08b}" for char in text])
        padding_length = (6 - len(binary) % 6) % 6
        binary += "0" * padding_length
        indices = [
            int(binary[index : index + 6], 2) for index in range(0, len(binary), 6)
        ]
        alphabet = self.alphabets[alphabet_index]
        return "".join([alphabet[index] for index in indices]) + "=" * (
            padding_length // 2
        )

    def abogus_encode(self, text: str, alphabet_index: int) -> str:
        """把混淆后的字节串编码成最终的 a_bogus 值。"""

        encoded: list[str] = []
        alphabet = self.alphabets[alphabet_index]
        for index in range(0, len(text), 3):
            if index + 2 < len(text):
                chunk = (
                    ord(text[index]) << 16
                    | ord(text[index + 1]) << 8
                    | ord(text[index + 2])
                )
            elif index + 1 < len(text):
                chunk = ord(text[index]) << 16 | ord(text[index + 1]) << 8
            else:
                chunk = ord(text[index]) << 16
            for shift, mask in zip((18, 12, 6, 0), (16515072, 258048, 4032, 63)):
                if shift == 6 and index + 1 >= len(text):
                    break
                if shift == 0 and index + 2 >= len(text):
                    break
                encoded.append(alphabet[(chunk & mask) >> shift])
        encoded.append("=" * ((4 - len(encoded) % 4) % 4))
        return "".join(encoded)

    @staticmethod
    def rc4_encrypt(key: bytes, plaintext: str) -> bytes:
        """用 RC4 加密字符串，返回密文字节。"""

        state = list(range(256))
        j = 0
        for i in range(256):
            j = (j + state[i] + key[i % len(key)]) % 256
            state[i], state[j] = state[j], state[i]

        i = 0
        j = 0
        ciphertext: list[int] = []
        for char in plaintext:
            i = (i + 1) % 256
            j = (j + state[i]) % 256
            state[i], state[j] = state[j], state[i]
            ciphertext.append(ord(char) ^ state[(state[i] + state[j]) % 256])
        return bytes(ciphertext)


def generate_browser_fingerprint(platform: str = "MacIntel") -> str:
    """生成与抖音 JS 同格式的浏览器指纹字符串。

    Args:
        platform: 指纹中的操作系统标识，需要与请求 User-Agent 的平台一致。

    Returns:
        由窗口尺寸、屏幕尺寸与平台拼成的指纹字符串。
    """

    inner_width = random.randint(1024, 1920)
    inner_height = random.randint(768, 1080)
    outer_width = inner_width + random.randint(24, 32)
    outer_height = inner_height + random.randint(75, 90)
    screen_y = random.choice([0, 30])
    size_width = random.randint(1024, 1920)
    size_height = random.randint(768, 1080)
    avail_width = random.randint(1280, 1920)
    avail_height = random.randint(800, 1080)
    return (
        f"{inner_width}|{inner_height}|{outer_width}|{outer_height}|0|{screen_y}"
        f"|0|0|{size_width}|{size_height}|{avail_width}|{avail_height}"
        f"|{inner_width}|{inner_height}|24|24|{platform}"
    )


def generate_abogus(
    params: str,
    *,
    user_agent: str = "",
    browser_fp: str = "",
    body: str = "",
) -> str:
    """计算抖音 web 接口所需的 a_bogus 签名值。

    Args:
        params: 参与签名的查询串，需与最终请求的查询串（不含 ``a_bogus``）一致。
        user_agent: 请求使用的 User-Agent，必须与随后发请求时的 UA 相同。
        browser_fp: 浏览器指纹，留空时按 Mac UA 随机生成。
        body: 参与签名的请求体，GET 请求留空。

    Returns:
        可直接作为 ``a_bogus`` 查询参数的签名值。
    """

    return _ABogus(
        fp=browser_fp or generate_browser_fingerprint(),
        user_agent=user_agent or _DEFAULT_USER_AGENT,
    ).generate(params, body)


class _ABogus:
    """a_bogus 签名的编码流程，字段含义与抖音 JS 实现一一对应。"""

    def __init__(self, *, fp: str, user_agent: str) -> None:
        """保存签名所需的固定参数与浏览器环境。

        Args:
            fp: 浏览器指纹。
            user_agent: 请求 User-Agent。
        """

        self.aid = 6383
        self.page_id = 0
        self.salt = "cus"
        self.options = [0, 1, 14]
        self.crypto = _CryptoUtility(self.salt, _ALPHABETS)
        self.user_agent = user_agent
        self.browser_fp = fp

    def generate(self, params: str, body: str) -> str:
        """按抖音 JS 的字段布局生成 a_bogus。

        Args:
            params: 参与签名的查询串。
            body: 参与签名的请求体。

        Returns:
            签名值。
        """

        # 抖音 JS 里还有 15 号（pageId、paths、track 等）与 19 号字段，当前版本既不
        # 参与排序也不参与异或，这里省略以免维护死数据。
        fields: dict[int, int] = {
            8: 3,
            18: 44,
            66: 0,
            69: 0,
            70: 0,
            71: 0,
        }

        start_encryption = int(time.time() * 1000)
        params_hash = self.crypto.params_to_array(self.crypto.params_to_array(params))
        body_hash = self.crypto.params_to_array(self.crypto.params_to_array(body))
        ua_hash = self.crypto.params_to_array(
            self.crypto.base64_encode(
                _codes_to_chars(self.crypto.rc4_encrypt(_UA_KEY, self.user_agent)),
                1,
            ),
            add_salt=False,
        )
        end_encryption = int(time.time() * 1000)

        fields[20] = start_encryption >> 24 & 255
        fields[21] = start_encryption >> 16 & 255
        fields[22] = start_encryption >> 8 & 255
        fields[23] = start_encryption & 255
        fields[24] = int(start_encryption / 256 / 256 / 256 / 256) >> 0
        fields[25] = int(start_encryption / 256 / 256 / 256 / 256 / 256) >> 0
        fields[26] = self.options[0] >> 24 & 255
        fields[27] = self.options[0] >> 16 & 255
        fields[28] = self.options[0] >> 8 & 255
        fields[29] = self.options[0] & 255
        fields[30] = int(self.options[1] / 256) & 255
        fields[31] = self.options[1] % 256 & 255
        fields[32] = self.options[1] >> 24 & 255
        fields[33] = self.options[1] >> 16 & 255
        fields[34] = self.options[2] >> 24 & 255
        fields[35] = self.options[2] >> 16 & 255
        fields[36] = self.options[2] >> 8 & 255
        fields[37] = self.options[2] & 255
        fields[38] = params_hash[21]
        fields[39] = params_hash[22]
        fields[40] = body_hash[21]
        fields[41] = body_hash[22]
        fields[42] = ua_hash[23]
        fields[43] = ua_hash[24]
        fields[44] = end_encryption >> 24 & 255
        fields[45] = end_encryption >> 16 & 255
        fields[46] = end_encryption >> 8 & 255
        fields[47] = end_encryption & 255
        fields[48] = fields[8]
        fields[49] = int(end_encryption / 256 / 256 / 256 / 256) >> 0
        fields[50] = int(end_encryption / 256 / 256 / 256 / 256 / 256) >> 0
        fields[51] = self.page_id >> 24 & 255
        fields[52] = self.page_id >> 16 & 255
        fields[53] = self.page_id >> 8 & 255
        fields[54] = self.page_id & 255
        fields[55] = self.page_id
        fields[56] = self.aid
        fields[57] = self.aid & 255
        fields[58] = self.aid >> 8 & 255
        fields[59] = self.aid >> 16 & 255
        fields[60] = self.aid >> 24 & 255
        fields[64] = len(self.browser_fp)
        fields[65] = len(self.browser_fp)

        values = [fields.get(index, 0) for index in _SORT_INDEX]
        xor_value = (len(self.browser_fp) & 255) >> 8 & 255
        for index in range(len(_SORT_INDEX_2) - 1):
            if index == 0:
                xor_value = fields.get(_SORT_INDEX_2[index], 0)
            xor_value ^= fields.get(_SORT_INDEX_2[index + 1], 0)

        values.extend(_chars_to_codes(self.browser_fp))
        values.append(xor_value)
        payload = _random_bytes() + self.crypto.transform_bytes(values)
        return self.crypto.abogus_encode(payload, 0)
